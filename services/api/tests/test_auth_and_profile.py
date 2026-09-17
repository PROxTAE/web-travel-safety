"""Auth negatives, JWKS rotation, ownership, profile/consent/emergency profile encryption, data jobs."""

import base64

import pytest
import respx

from tests.conftest import AUD, ISSUER, KEY, OTHER_KEY, Client, auth, jwks_doc, token


@pytest.mark.asyncio
@respx.mock
async def test_auth_negative_matrix(settings, stubs):
    async with Client(settings, stubs) as c:
        h = c.http
        assert (await h.get("/api/v1/me")).status_code == 401
        assert (await h.get("/api/v1/me", headers={"Authorization": "Bearer not.a.jwt"})).status_code == 401
        r = await h.get("/api/v1/me", headers=auth(exp_delta=-120))
        assert r.status_code == 401 and r.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
        assert (await h.get("/api/v1/me", headers=auth(iss="http://evil.test/realms/x"))).status_code == 401
        assert (await h.get("/api/v1/me", headers=auth(aud="other-api"))).status_code == 401
        # signed by a key that is not in the JWKS (same kid => signature failure)
        assert (await h.get("/api/v1/me", headers=auth(key=OTHER_KEY))).status_code == 401
        # missing required role => 403, not 401
        r = await h.get("/api/v1/me", headers=auth(roles=("safety-reviewer",)))
        assert r.status_code == 403
        # alg=none is rejected before any key lookup
        import jwt as pyjwt

        none_token = pyjwt.encode(
            {"iss": ISSUER, "sub": "x", "aud": AUD, "exp": 9999999999, "iat": 1}, key=None, algorithm="none"
        )
        assert (await h.get("/api/v1/me", headers={"Authorization": f"Bearer {none_token}"})).status_code == 401
        # valid
        r = await h.get("/api/v1/me", headers=auth())
        assert r.status_code == 200 and r.json()["data"]["locale"] == "en-US"
        assert "X-Correlation-ID" in r.headers and r.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
@respx.mock
async def test_jwks_rotation_refreshes_once_for_unknown_kid(settings, stubs):
    async with Client(settings, stubs) as c:
        assert (await c.http.get("/api/v1/me", headers=auth())).status_code == 200
        calls = stubs.jwks_calls
        # rotate the realm key: new kid published, old one gone
        stubs.jwks = jwks_doc(OTHER_KEY, "test-kid-2")
        r = await c.http.get("/api/v1/me", headers=auth(key=OTHER_KEY, kid="test-kid-2"))
        assert r.status_code == 200 and stubs.jwks_calls == calls + 1
        # old key now rejected; an unknown kid does not trigger a refresh storm
        for _ in range(5):
            assert (await c.http.get("/api/v1/me", headers=auth(key=KEY, kid="ghost"))).status_code == 401
        assert stubs.jwks_calls <= calls + 2


@pytest.mark.asyncio
@respx.mock
async def test_profile_consents_and_encrypted_emergency_profile(settings, stubs):
    async with Client(settings, stubs) as c:
        h = c.http
        r = await h.patch("/api/v1/me", json={"locale": "th-TH", "timezone": "Asia/Bangkok"}, headers=auth())
        assert r.status_code == 200 and r.json()["data"]["timezone"] == "Asia/Bangkok"
        assert (await h.patch("/api/v1/me", json={"timezone": "Mars/Olympus"}, headers=auth())).status_code == 422
        # emergency profile requires consent
        profile = {
            "blood_type": "O+",
            "allergies": ["penicillin"],
            "medical_notes": "asthma",
            "contacts": [{"name": "A", "phone": "+66812345678"}],
        }
        assert (await h.put("/api/v1/me/emergency-profile", json=profile, headers=auth())).status_code == 403
        r = await h.post(
            "/api/v1/consents",
            json={"type": "EMERGENCY_PROFILE", "granted": True, "policy_version": "2026-09"},
            headers=auth(),
        )
        assert r.status_code == 201 and r.json()["data"]["granted"] is True
        r = await h.put("/api/v1/me/emergency-profile", json=profile, headers=auth())
        assert r.status_code == 200 and r.json()["data"]["stored"] and "asthma" not in r.text
        # stored encrypted: no plaintext in the row
        rows = list(c.app.state.repo.store._mem["identity.emergency_profiles"].values())
        assert (
            rows and b"penicillin" not in rows[0]["encrypted_payload"] and b"asthma" not in rows[0]["encrypted_payload"]
        )
        r = await h.get("/api/v1/me/emergency-profile", headers=auth())
        assert r.status_code == 200 and r.json()["data"]["allergies"] == ["penicillin"]
        # other user cannot read it
        assert (await h.get("/api/v1/me/emergency-profile", headers=auth("user-2"))).status_code == 404
        me = (await h.get("/api/v1/me", headers=auth())).json()["data"]
        assert me["has_emergency_profile"] is True and me["consents"][0]["type"] == "EMERGENCY_PROFILE"
        # revoking the consent deletes the profile
        r = await h.post(
            "/api/v1/consents",
            json={"type": "EMERGENCY_PROFILE", "granted": False, "policy_version": "2026-09"},
            headers=auth(),
        )
        assert r.status_code == 201 and r.json()["data"]["revoked_at"]
        assert (await h.get("/api/v1/me/emergency-profile", headers=auth())).status_code == 404
        # audit log never stores content
        audit = await c.app.state.repo.audit.for_user(me_id := __import__("uuid").UUID(me["id"]))
        assert any(a["action"] == "emergency_profile.update" for a in audit)
        assert "penicillin" not in str(audit) and "asthma" not in str(audit)
        assert me_id


@pytest.mark.asyncio
@respx.mock
async def test_data_jobs_export_and_delete(settings, stubs):
    async with Client(settings, stubs) as c:
        h = c.http
        await c.create_trip()
        r = await h.post("/api/v1/me/data-jobs", json={"kind": "EXPORT"}, headers=auth())
        assert r.status_code == 202 and r.json()["data"]["status"] == "COMPLETED"
        assert len(r.json()["data"]["result"]["trips"]) == 1
        job = r.json()["data"]["id"]
        assert (await h.get(f"/api/v1/me/data-jobs/{job}", headers=auth("user-2"))).status_code == 404
        r = await h.post("/api/v1/me/data-jobs", json={"kind": "DELETE"}, headers=auth())
        assert r.status_code == 202 and r.json()["data"]["status"] == "SCHEDULED"
        # account scheduled for deletion is locked out
        assert (await h.get("/api/v1/me", headers=auth())).status_code == 403


def test_encryption_key_length_enforced(settings):
    from app.domain.crypto import ProfileCipher

    with pytest.raises(ValueError):
        ProfileCipher(base64.b64encode(b"short").decode(), 1)
    c = ProfileCipher(base64.b64encode(b"k" * 32).decode(), 1)
    blob, v = c.encrypt({"a": 1}, aad="u1")
    assert c.decrypt(blob, key_version=v, aad="u1") == {"a": 1}
    from sta_common.errors import AppError

    with pytest.raises(AppError):
        c.decrypt(blob, key_version=v, aad="u2")  # bound to the owner


def test_token_helper_is_valid_rs256():
    import jwt as pyjwt

    claims = pyjwt.decode(token(), KEY.public_key(), algorithms=["RS256"], audience=AUD, issuer=ISSUER)
    assert claims["sub"] == "user-1"
