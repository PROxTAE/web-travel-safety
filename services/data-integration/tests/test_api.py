"""API tests: idempotent create, get, validate, events search, auth."""

import pytest
from httpx import ASGITransport, AsyncClient
from sta_contracts.models import SnapshotCreateRequest

from app.main import build
from app.settings import Settings
from tests.conftest import BKK, CNX, context, event, travel_request


@pytest.mark.asyncio
async def test_create_get_validate_idempotent():
    app = build(Settings(app_env="test"))
    req = travel_request()
    mid = [(BKK[0] + CNX[0]) / 2, (BKK[1] + CNX[1]) / 2]
    ctx = context(req, events=[event("usgs:api1", [mid[0] + 0.05, mid[1]])])
    body = SnapshotCreateRequest(travel_request=req, external_context=ctx).model_dump(mode="json")
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r1 = await c.post("/internal/v1/snapshots", json=body)
            assert r1.status_code == 201, r1.text
            snap = r1.json()["data"]
            assert snap["quality_summary"]["gate"] in ("PASS", "DEGRADED")
            assert snap["features"]["active_disaster_event_count"] == 1
            r2 = await c.post("/internal/v1/snapshots", json=body)
            assert r2.status_code == 201 and r2.json()["data"]["snapshot_id"] == snap["snapshot_id"]  # idempotent
            g = await c.get(f"/internal/v1/snapshots/{snap['snapshot_id']}")
            assert g.status_code == 200 and g.json()["data"]["content_hash"] == snap["content_hash"]
            v = await c.post(f"/internal/v1/snapshots/{snap['snapshot_id']}/validate", json={"strict": True})
            assert v.status_code == 200 and v.json()["data"]["gate"] == snap["quality_summary"]["gate"]
            missing = await c.get("/internal/v1/snapshots/00000000-0000-0000-0000-000000000000")
            assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"
            ev = await c.get("/internal/v1/events/search", params={"bbox": "95,5,110,22"})
            assert ev.status_code == 200 and ev.json()["data"][0]["event_id"] == "usgs:api1"
            bad = await c.get("/internal/v1/events/search", params={"bbox": "1,2"})
            assert bad.status_code == 422
            fs = await c.get("/internal/v1/feature-schema")
            assert fs.json()["data"]["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_supersedes_links_new_snapshot_for_same_trip():
    app = build(Settings(app_env="test"))
    req = travel_request()
    ctx1 = context(req)
    req2 = req.model_copy(update={"request_id": __import__("uuid").uuid4(), "trip_revision": 2})
    ctx2 = context(req2)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            a = await c.post(
                "/internal/v1/snapshots",
                json=SnapshotCreateRequest(travel_request=req, external_context=ctx1).model_dump(mode="json"),
            )
            b = await c.post(
                "/internal/v1/snapshots",
                json=SnapshotCreateRequest(travel_request=req2, external_context=ctx2).model_dump(mode="json"),
            )
            assert b.json()["data"]["supersedes_snapshot_id"] == a.json()["data"]["snapshot_id"]
            assert b.json()["data"]["trip_revision"] == 2


@pytest.mark.asyncio
async def test_internal_auth_required():
    app = build(Settings(app_env="test", service_auth_token="tok"))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.get("/internal/v1/feature-schema")).status_code == 401
            assert (
                await c.get("/internal/v1/feature-schema", headers={"Authorization": "Bearer tok"})
            ).status_code == 200
