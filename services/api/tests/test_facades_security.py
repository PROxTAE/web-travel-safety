"""Locations, safety map, emergency, feedback, subscriptions, follow-up messages, rate limiting, privacy, contract."""

import json
import logging
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import respx

from tests.conftest import Client, auth


@pytest.mark.asyncio
@respx.mock
async def test_locations_search_proxy_and_validation(settings, stubs):
    async with Client(settings, stubs) as c:
        h = c.http
        assert (await h.get("/api/v1/locations/search?q=a", headers=auth())).status_code == 422
        r = await h.get("/api/v1/locations/search?q=Chiang%20Mai&locale=th-TH&country=TH", headers=auth())
        assert r.status_code == 200 and r.json()["data"][0]["confirmed_by_user"] is False
        assert r.headers["Cache-Control"] == "private, max-age=300"
        stubs.fail["geocode"] = httpx.Response(504, json={"error": {"code": "DEPENDENCY_TIMEOUT", "message": "slow"}})
        r = await h.get("/api/v1/locations/search?q=Chiang%20Mai", headers=auth())
        assert r.status_code == 504 and r.json()["error"]["code"] == "DEPENDENCY_TIMEOUT"


@pytest.mark.asyncio
@respx.mock
async def test_safety_events_bbox_validation_and_degraded_passthrough(settings, stubs):
    async with Client(settings, stubs) as c:
        h = c.http
        assert (await h.get("/api/v1/safety/events?bbox=1,2,3", headers=auth())).status_code == 422
        assert (await h.get("/api/v1/safety/events?bbox=100,13,90,19", headers=auth())).status_code == 422
        assert (await h.get("/api/v1/safety/events?bbox=80,0,120,30", headers=auth())).status_code == 422
        assert (await h.get("/api/v1/safety/events?bbox=98,13,101,19&layers=ufo", headers=auth())).status_code == 422
        r = await h.get("/api/v1/safety/events?bbox=98,13,101,19&layers=disasters", headers=auth())
        assert r.status_code == 200 and set(r.json()["data"]["layers"]) == {"disasters"}
        assert r.json()["meta"]["degraded_services"] == ["gdacs: timeout"]


@pytest.mark.asyncio
@respx.mock
async def test_emergency_contacts_and_nearby_requires_consent_and_coarsens_coordinates(settings, stubs):
    async with Client(settings, stubs) as c:
        h = c.http
        r = await h.get("/api/v1/emergency/contacts?country_code=th", headers=auth())
        assert r.status_code == 200 and r.json()["data"]["contacts"][0]["phone"] == "1669"
        assert r.json()["data"]["directory_version"] == "2026.09.1"
        r = await h.get("/api/v1/emergency/nearby?type=MEDICAL&lat=13.7563123&lon=100.5018456", headers=auth())
        assert r.status_code == 403
        await h.post(
            "/api/v1/consents", json={"type": "LOCATION_ONCE", "granted": True, "policy_version": "1"}, headers=auth()
        )
        r = await h.get("/api/v1/emergency/nearby?type=MEDICAL&lat=13.7563123&lon=100.5018456", headers=auth())
        assert r.status_code == 200 and r.json()["data"]["type"] == "FeatureCollection"
        assert stubs.last_nearby["location"]["coordinates"] == [100.5018, 13.7563]  # 4 dp ≈ 11 m, not exact fix
        assert (await h.get("/api/v1/emergency/nearby?type=BANK&lat=1&lon=1", headers=auth())).status_code == 422


@pytest.mark.asyncio
@respx.mock
async def test_feedback_subscriptions_and_followup(settings, stubs):
    async with Client(settings, stubs) as c:
        h = c.http
        trip, _ = await c.create_trip()
        tid = trip["id"]
        ref = (await h.post(f"/api/v1/trips/{tid}/assessments", headers=auth())).json()["data"]
        st = await c.wait_run(ref["request_id"])
        rec_id = st["recommendation_id"]
        # feedback proxies with the pseudonymous scope; other user cannot attach feedback
        r = await h.post(
            "/api/v1/feedback",
            json={"recommendation_id": rec_id, "category": "UNSAFE", "text": "call 0812345678"},
            headers=auth(),
        )
        assert r.status_code == 201 and r.json()["data"]["review_status"] == "QUEUED"
        assert stubs.last_feedback["user_scope"] == stubs.runs[ref["request_id"]]["user_scope_hash"]
        assert (
            await h.post(
                "/api/v1/feedback", json={"recommendation_id": rec_id, "category": "HELPFUL"}, headers=auth("user-2")
            )
        ).status_code == 404
        # subscriptions need an active ALERT_NOTIFICATION consent that belongs to the caller
        sub_body = {"trip_id": tid, "channel": "IN_APP", "consent_id": "00000000-0000-0000-0000-000000000009"}
        assert (await h.post("/api/v1/alert-subscriptions", json=sub_body, headers=auth())).status_code == 403
        consent = (
            await h.post(
                "/api/v1/consents",
                json={"type": "ALERT_NOTIFICATION", "granted": True, "policy_version": "1"},
                headers=auth(),
            )
        ).json()["data"]
        r = await h.post("/api/v1/alert-subscriptions", json={**sub_body, "consent_id": consent["id"]}, headers=auth())
        assert r.status_code == 201 and r.json()["data"]["status"] == "ACTIVE"
        sid = r.json()["data"]["id"]
        assert (
            await h.post(
                "/api/v1/alert-subscriptions",
                json={**sub_body, "consent_id": consent["id"], "channel": "SMS"},
                headers=auth(),
            )
        ).status_code == 422
        assert (await h.delete(f"/api/v1/alert-subscriptions/{sid}", headers=auth("user-2"))).status_code == 404
        assert (await h.delete(f"/api/v1/alert-subscriptions/{sid}", headers=auth())).status_code == 204
        # revoking the consent cascades to the recommendation service
        await h.post(
            "/api/v1/consents",
            json={"type": "ALERT_NOTIFICATION", "granted": False, "policy_version": "1"},
            headers=auth(),
        )
        assert stubs.calls["revoke_consent"] >= 1
        # follow-up message => new run on the same conversation
        conv = (await h.get("/api/v1/conversations", headers=auth())).json()["data"][0]
        r = await h.post(
            f"/api/v1/conversations/{conv['id']}/messages", json={"text": "why did you say that?"}, headers=auth()
        )
        assert r.status_code == 202 and r.json()["data"]["run"]["conversation_id"] == conv["id"]
        assert stubs.runs[r.json()["data"]["run"]["request_id"]]["question"] == "why did you say that?"
        assert (
            await h.post(f"/api/v1/conversations/{conv['id']}/messages", json={"text": "hi"}, headers=auth("user-2"))
        ).status_code == 404
        assert (
            await h.post(f"/api/v1/conversations/{conv['id']}/messages", json={"text": "  \x00 "}, headers=auth())
        ).status_code == 422


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_per_user_and_endpoint_class(settings, stubs):
    s = settings.model_copy(update={"rate_limit_search_per_minute": 2})
    async with Client(s, stubs) as c:
        h = c.http
        for _ in range(2):
            assert (await h.get("/api/v1/locations/search?q=Chiang", headers=auth())).status_code == 200
        r = await h.get("/api/v1/locations/search?q=Chiang", headers=auth())
        assert (
            r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED" and int(r.headers["Retry-After"]) >= 1
        )
        # other user and other endpoint class unaffected
        assert (await h.get("/api/v1/locations/search?q=Chiang", headers=auth("user-2"))).status_code == 200
        assert (await h.get("/api/v1/me", headers=auth())).status_code == 200


@pytest.mark.asyncio
@respx.mock
async def test_logs_never_contain_tokens_coordinates_or_medical_data(settings, stubs, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    async with Client(settings, stubs) as c:
        h = c.http
        hdr = auth()
        await h.post(
            "/api/v1/consents", json={"type": "EMERGENCY_PROFILE", "granted": True, "policy_version": "1"}, headers=hdr
        )
        await h.put("/api/v1/me/emergency-profile", json={"medical_notes": "diabetes-type-1-secret"}, headers=hdr)
        trip, _ = await c.create_trip()
        ref = (
            await h.post(
                f"/api/v1/trips/{trip['id']}/assessments", json={"question": "secret question 0891234567"}, headers=hdr
            )
        ).json()["data"]
        await c.wait_run(ref["request_id"])
    out = capsys.readouterr()
    blob = caplog.text + out.out + out.err
    tok = hdr["Authorization"].split(" ", 1)[1]
    assert tok not in blob and "diabetes-type-1-secret" not in blob
    assert "100.5018" not in blob and "13.7563" not in blob and "secret question" not in blob


@pytest.mark.asyncio
@respx.mock
async def test_request_body_limit_and_health(settings, stubs):
    async with Client(settings, stubs) as c:
        big = {"question": "x" * 300_000}
        r = await c.http.post(
            "/api/v1/trips/00000000-0000-0000-0000-000000000000/assessments", json=big, headers=auth()
        )
        assert r.status_code == 413
        live = await c.http.get("/health/live")
        assert live.status_code == 200 and live.json()["service"] == "api"
        ready = await c.http.get("/health/ready")
        assert ready.status_code == 200 and ready.json()["dependencies"]["oidc"]["status"] == "up"


def test_openapi_snapshot_in_sync(settings):
    script = Path(__file__).resolve().parents[1] / "scripts" / "export_openapi.py"
    res = subprocess.run([sys.executable, str(script), "--check"], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_openapi_covers_public_contract_matrix(settings):
    from app.main import build, openapi_document

    doc = openapi_document(build(settings))
    paths = doc["paths"]
    expected = [
        ("get", "/api/v1/me"),
        ("patch", "/api/v1/me"),
        ("get", "/api/v1/me/emergency-profile"),
        ("put", "/api/v1/me/emergency-profile"),
        ("post", "/api/v1/consents"),
        ("get", "/api/v1/locations/search"),
        ("post", "/api/v1/trips"),
        ("get", "/api/v1/trips/{trip_id}"),
        ("patch", "/api/v1/trips/{trip_id}"),
        ("delete", "/api/v1/trips/{trip_id}"),
        ("post", "/api/v1/trips/{trip_id}/assessments"),
        ("get", "/api/v1/runs/{request_id}"),
        ("get", "/api/v1/runs/{request_id}/events"),
        ("get", "/api/v1/recommendations/{rec_id}"),
        ("post", "/api/v1/trips/{trip_id}/apply-route"),
        ("get", "/api/v1/safety/events"),
        ("get", "/api/v1/conversations"),
        ("post", "/api/v1/conversations/{conv_id}/messages"),
        ("post", "/api/v1/feedback"),
        ("post", "/api/v1/alert-subscriptions"),
        ("delete", "/api/v1/alert-subscriptions/{sub_id}"),
        ("get", "/api/v1/emergency/contacts"),
        ("get", "/api/v1/emergency/nearby"),
    ]
    missing = [(m, p) for m, p in expected if p not in paths or m not in paths[p]]
    assert not missing, missing
    assert doc["security"] == [{"oidc": []}]
    assert json.dumps(doc)  # serializable
