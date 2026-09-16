import pytest
from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient

from sta_common.app import create_app
from sta_common.errors import AppError, ErrorCode
from sta_common.health import HealthRegistry
from sta_common.settings import BaseServiceSettings


def _app(ready_ok: bool = True):
    settings = BaseServiceSettings(service_name="test", app_env="test")
    health = HealthRegistry("test", "0.0.0")

    async def dep():
        if not ready_ok:
            raise RuntimeError("down")

    health.add("db", dep)
    app = create_app(settings, title="t", version="0.0.0", health=health)
    r = APIRouter()

    @r.get("/boom")
    async def boom():
        raise AppError(ErrorCode.DEPENDENCY_TIMEOUT, "upstream slow")

    @r.post("/boom")
    async def boom_post():
        return {"ok": True}

    @r.get("/crash")
    async def crash():
        raise RuntimeError("secret sql SELECT * FROM x")

    @r.get("/leak")
    async def leak():
        raise AppError(ErrorCode.VALIDATION_ERROR, "email a@b.co rejected")

    app.include_router(r)
    return app


@pytest.mark.asyncio
async def test_error_envelope_and_headers():
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.get("/boom", headers={"X-Request-ID": "req-12345678"})
        assert r.status_code == 504
        body = r.json()
        assert body["error"]["code"] == "DEPENDENCY_TIMEOUT"
        assert body["error"]["retryable"] is True
        assert body["meta"]["request_id"] == "req-12345678"
        assert r.headers["X-Request-ID"] == "req-12345678"


@pytest.mark.asyncio
async def test_unhandled_does_not_leak():
    transport = ASGITransport(app=_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/crash")
        assert r.status_code == 500
        assert "SELECT" not in r.text


@pytest.mark.asyncio
async def test_error_message_redacted():
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.get("/leak")
        assert "a@b.co" not in r.text


@pytest.mark.asyncio
async def test_health_live_and_ready():
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        assert (await c.get("/health/live")).status_code == 200
        assert (await c.get("/health/ready")).status_code == 200
    async with AsyncClient(transport=ASGITransport(app=_app(ready_ok=False)), base_url="http://t") as c:
        assert (await c.get("/health/live")).status_code == 200
        r = await c.get("/health/ready")
        assert r.status_code == 503
        assert r.json()["dependencies"]["db"]["status"] == "down"


@pytest.mark.asyncio
async def test_payload_too_large():
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.post("/boom", content=b"x", headers={"content-length": "99999999"})
        assert r.status_code == 413
