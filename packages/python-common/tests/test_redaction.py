from sta_common.redaction import REDACTED, redact, redact_text


def test_redacts_email_phone_tokens():
    s = "contact a.b@example.com or +66 81 234 5678, token Bearer abc.def.ghi"
    out = redact_text(s)
    assert "example.com" not in out
    assert "234" not in out
    assert "abc.def.ghi" not in out


def test_redacts_jwt_and_openai_key():
    jwt = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnopqrstuvwxyz0123456789"
    assert REDACTED in redact_text(jwt)
    assert REDACTED in redact_text("sk-abcdefghijklmnopqrstuvwxyz")


def test_redacts_sensitive_keys_and_coarsens_coordinates():
    out = redact({"password": "x", "coordinates": [100.501234, 13.756789], "nested": {"email": "a@b.co"}})
    assert out["password"] == REDACTED
    assert out["coordinates"] == [100.5, 13.76]
    assert out["nested"]["email"] == REDACTED


def test_zero_and_bool_preserved():
    out = redact({"count": 0, "flag": True, "lat": 0.0})
    assert out == {"count": 0, "flag": True, "lat": 0.0}
