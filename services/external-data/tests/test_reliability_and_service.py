"""Reliability (circuit breaker, 429/5xx/timeout mapping), geodesic/sampling, combined context and API."""

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from sta_contracts.enums import QualityFlag, TravelMode
from sta_contracts.geo import Point
from sta_contracts.models import ContextQuery, LocationRef

from app.adapters.base import ProviderError, ProviderErrorCode
from app.adapters.usgs import UsgsAdapter
from app.main import build
from app.services.geodesic import geodesic_route
from app.services.route_sampling import sample_route
from app.settings import Settings

NOW = datetime.now(UTC)
SVC = "external-data-test"


def _loc(lon: float, lat: float, name: str) -> LocationRef:
    return LocationRef(
        place_id=name,
        display_name=name,
        coordinates=Point(coordinates=[lon, lat]),
        country_code="TH",
        timezone="Asia/Bangkok",
        provider="open_meteo_geocoding",
        confirmed_by_user=True,
    )


BKK = _loc(100.5018, 13.7563, "Bangkok")
CNX = _loc(98.9853, 18.7883, "Chiang Mai")


# ------------------------------------------------------------------ reliability
@pytest.mark.asyncio
@respx.mock
async def test_429_maps_to_rate_limit_with_retry_after():
    respx.get("https://usgs.test/q").mock(return_value=httpx.Response(429, headers={"Retry-After": "7"}))
    a = UsgsAdapter("https://usgs.test/q", service_name=SVC, ttl=600, min_magnitude=2.5)
    a.client.max_retries = 0
    with pytest.raises(ProviderError) as ei:
        await a.query(
            __import__("sta_contracts.geo", fromlist=["BBox"]).BBox(min_lon=0, min_lat=0, max_lon=1, max_lat=1),
            lookback_days=1,
        )
    assert ei.value.code == ProviderErrorCode.PROVIDER_RATE_LIMIT and ei.value.retry_after == 7
    assert ei.value.to_app_error().status_code == 429
    await a.close()


@pytest.mark.asyncio
@respx.mock
async def test_circuit_opens_after_threshold_and_half_opens():
    from sta_contracts.geo import BBox

    route = respx.get("https://usgs.test/q").mock(return_value=httpx.Response(503))
    a = UsgsAdapter(
        "https://usgs.test/q", service_name=SVC, ttl=600, min_magnitude=2.5, failure_threshold=2, open_seconds=60
    )
    a.client.max_retries = 0
    bbox = BBox(min_lon=0, min_lat=0, max_lon=1, max_lat=1)
    for _ in range(2):
        with pytest.raises(ProviderError):
            await a.query(bbox, lookback_days=1)
    assert a.health.status == "DOWN"
    with pytest.raises(ProviderError) as ei:
        await a.query(bbox, lookback_days=1)
    assert ei.value.message.endswith("circuit open") and route.call_count == 2  # no extra provider call while open
    a.health.opened_at -= 61  # simulate time passing
    route.mock(return_value=httpx.Response(200, json={"features": []}))
    assert await a.query(bbox, lookback_days=1) == []
    assert a.health.status == "UP"
    await a.close()


@pytest.mark.asyncio
@respx.mock
async def test_timeout_maps_to_provider_timeout():
    from sta_contracts.geo import BBox

    respx.get("https://usgs.test/q").mock(side_effect=httpx.ReadTimeout("slow"))
    a = UsgsAdapter("https://usgs.test/q", service_name=SVC, ttl=600, min_magnitude=2.5)
    a.client.max_retries = 0
    with pytest.raises(ProviderError) as ei:
        await a.query(BBox(min_lon=0, min_lat=0, max_lon=1, max_lat=1), lookback_days=1)
    assert ei.value.code == ProviderErrorCode.PROVIDER_TIMEOUT
    await a.close()


@pytest.mark.asyncio
@respx.mock
async def test_non_json_body_is_schema_changed():
    from sta_contracts.geo import BBox

    respx.get("https://usgs.test/q").mock(return_value=httpx.Response(200, content=b"<html>oops</html>"))
    a = UsgsAdapter("https://usgs.test/q", service_name=SVC, ttl=600, min_magnitude=2.5)
    with pytest.raises(ProviderError) as ei:
        await a.query(BBox(min_lon=0, min_lat=0, max_lon=1, max_lat=1), lookback_days=1)
    assert ei.value.code == ProviderErrorCode.PROVIDER_SCHEMA_CHANGED
    await a.close()


# ------------------------------------------------------------------ geodesic + sampling
def test_geodesic_route_is_flagged_inferred_and_reasonable():
    r = geodesic_route(
        BKK.coordinates, CNX.coordinates, TravelMode.TRAIN, departure_time=NOW, reason="no provider", ttl_seconds=3600
    )
    assert 560_000 < r.distance_m < 600_000  # BKK->CNX great-circle ~ 580 km
    assert QualityFlag.INFERRED in r.quality.flags and QualityFlag.OUTSIDE_COVERAGE in r.quality.flags
    assert r.quality.score < 0.5 and "approximation" in r.quality.notes[0]
    assert r.geometry.coordinates[0] == [100.5018, 13.7563] and len(r.geometry.coordinates) >= 20
    assert r.segments[0].eta_end > r.segments[0].eta_start


def test_route_sampling_caps_and_reports_coverage():
    r = geodesic_route(
        BKK.coordinates, CNX.coordinates, TravelMode.CAR, departure_time=NOW, reason="x", ttl_seconds=3600
    )
    samples, coverage = sample_route(r, departure_time=NOW, max_samples=6)
    assert len(samples) == 6 and 0 < coverage < 1
    assert samples[0].eta_at == NOW and samples[-1].eta_at > NOW
    assert samples[0].point.coordinates == pytest.approx([100.5018, 13.7563], abs=1e-4)
    assert samples[-1].point.coordinates == pytest.approx([98.9853, 18.7883], abs=1e-4)
    full, cov2 = sample_route(r, departure_time=NOW, max_samples=40)
    assert cov2 == 1.0 and len(full) >= 20


# ------------------------------------------------------------------ combined context + API
@pytest.mark.asyncio
@respx.mock
async def test_context_query_degrades_honestly(fx, fx_bytes):
    """Weather OK, USGS down, GDACS/EONET OK, no ORS key, no GTFS coverage -> partial context, nothing fabricated."""
    payload = fx("open_meteo_forecast.bkk_cnx.json")
    respx.get("https://wx.test/v1/forecast").mock(return_value=httpx.Response(200, json=payload))
    respx.get("https://usgs.test/fdsnws/event/1/query").mock(return_value=httpx.Response(503))
    respx.get("https://gdacs.test/api/events/geteventlist/SEARCH").mock(
        return_value=httpx.Response(200, json=fx("gdacs.eventlist.json"))
    )
    respx.get("https://eonet.test/events").mock(return_value=httpx.Response(200, json=fx("eonet.open_events.json")))
    settings = Settings(
        app_env="test",
        open_meteo_base_url="https://wx.test",
        usgs_query_url="https://usgs.test/fdsnws/event/1/query",
        gdacs_base_url="https://gdacs.test",
        eonet_base_url="https://eonet.test",
        gtfs_provider_config="config/providers.yaml",
        ors_api_key="",
    )
    app = build(settings)
    dep = datetime.fromisoformat(payload[0]["hourly"]["time"][2]).replace(tzinfo=UTC)
    q = ContextQuery(
        request_id=uuid4(),
        trip_id=uuid4(),
        origin=BKK,
        destination=CNX,
        departure_time=dep,
        travel_modes=[TravelMode.TRAIN, TravelMode.CAR],
        max_weather_samples=2,  # fixture holds exactly 2 locations
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/internal/v1/context/query", json=q.model_dump(mode="json"))
            assert r.status_code == 200, r.text
            data = r.json()["data"]
            assert r.json()["meta"]["degraded_services"]
            assert any(d.startswith("usgs") for d in data["degraded_services"])
            assert any("ORS_API_KEY" in u for u in data["unavailable_capabilities"])
            assert any("GTFS" in u or "transit" in u for u in data["unavailable_capabilities"])
            # routes exist for both modes but are honestly flagged as approximations
            assert len(data["routes"]) == 2
            assert all("INFERRED" in rt["quality"]["flags"] for rt in data["routes"])
            # weather came from the real (fixture) provider along the corridor
            assert data["weather"] and all(w["source"]["provider"] == "open_meteo" for w in data["weather"])
            # transport is UNKNOWN/UNAVAILABLE, never "On time"
            assert data["transport"] and all(t["status"] == "UNKNOWN" for t in data["transport"])
            assert all(t["quality"]["status"] == "UNAVAILABLE" for t in data["transport"])
            # provider health is reported without secrets
            health = {h["provider"]: h for h in data["provider_health"]}
            assert health["openrouteservice"]["status"] == "UNAVAILABLE" and health["usgs"]["status"] in (
                "DEGRADED",
                "DOWN",
            )
            assert "key" not in r.text.lower() or "ORS_API_KEY" in r.text  # only the env var name may appear
            h = await c.get("/internal/v1/providers/health")
            assert h.status_code == 200 and len(h.json()["data"]) == 8


@pytest.mark.asyncio
async def test_internal_auth_enforced_when_token_set():
    settings = Settings(app_env="test", service_auth_token="s3cret")
    app = build(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/internal/v1/providers/health")
            assert r.status_code == 401 and r.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
            r = await c.get("/internal/v1/providers/health", headers={"Authorization": "Bearer s3cret"})
            assert r.status_code == 200
            assert (await c.get("/health/live")).status_code == 200


@pytest.mark.asyncio
async def test_places_without_ors_key_is_unsupported_coverage():
    app = build(Settings(app_env="test", ors_api_key=""))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/internal/v1/places/nearby",
                json={"location": {"type": "Point", "coordinates": [100.5, 13.7]}, "type": "MEDICAL"},
            )
            assert r.status_code == 422 and r.json()["error"]["code"] == "UNSUPPORTED_COVERAGE"


@pytest.mark.asyncio
async def test_validation_rejects_bad_coordinates():
    app = build(Settings(app_env="test"))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/internal/v1/places/nearby",
                json={"location": {"type": "Point", "coordinates": [13.7, 100.5]}, "type": "MEDICAL"},
            )
            assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
            assert r.json()["error"]["field_errors"]
