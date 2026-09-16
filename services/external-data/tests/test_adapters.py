"""Adapter unit tests: real sanitized provider payloads -> canonical records."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sta_contracts.enums import DataStatus, DisasterEventType, QualityFlag, Severity, SourceAuthority, TravelMode
from sta_contracts.geo import BBox, Point

from app.adapters.base import ProviderError, ProviderErrorCode
from app.adapters.eonet import EonetAdapter
from app.adapters.gdacs import GdacsAdapter
from app.adapters.gtfs import GtfsFeed, GtfsRealtimeAdapter, GtfsRegistry
from app.adapters.open_meteo_geocoding import OpenMeteoGeocodingAdapter
from app.adapters.open_meteo_weather import OpenMeteoWeatherAdapter, WeatherSample, _align
from app.adapters.openrouteservice import OpenRouteServiceAdapter
from app.adapters.usgs import UsgsAdapter
from app.domain.canonical import escalate_weather_severity, wmo_category

SVC = "external-data-test"
NOW = datetime.now(UTC)


# ------------------------------------------------------------------ geocoding
@pytest.mark.asyncio
@respx.mock
async def test_geocoding_maps_real_payload(fx):
    respx.get("https://geo.test/v1/search").mock(
        return_value=httpx.Response(200, json=fx("open_meteo_geocoding.chiang_mai.json"))
    )
    a = OpenMeteoGeocodingAdapter("https://geo.test", service_name=SVC)
    out = await a.search("Chiang Mai", locale="en-US")
    assert out and out[0].place_id == "1153671"
    assert out[0].country_code == "TH" and out[0].timezone == "Asia/Bangkok"
    lon, lat = out[0].coordinates.coordinates
    assert 98 < lon < 100 and 18 < lat < 19  # [lon, lat] order
    assert out[0].confirmed_by_user is False
    await a.close()


@pytest.mark.asyncio
@respx.mock
async def test_geocoding_no_results_is_empty_not_guessed():
    respx.get("https://geo.test/v1/search").mock(return_value=httpx.Response(200, json={"generationtime_ms": 0.1}))
    a = OpenMeteoGeocodingAdapter("https://geo.test", service_name=SVC)
    assert await a.search("zzzzqqq") == []
    assert await a.search("a") == []  # below min length: no call
    await a.close()


# ------------------------------------------------------------------ weather
def test_wmo_mapping_and_escalation():
    assert wmo_category(95) == ("THUNDERSTORM", Severity.SEVERE)
    assert wmo_category(None) == (None, Severity.UNKNOWN)
    assert wmo_category(12345) == ("UNKNOWN", Severity.UNKNOWN)
    # escalation only raises
    assert (
        escalate_weather_severity(Severity.INFO, wind_gust_kmh=120, precipitation_mm=0, visibility_m=None)
        == Severity.EXTREME
    )
    assert (
        escalate_weather_severity(Severity.SEVERE, wind_gust_kmh=0, precipitation_mm=0, visibility_m=9000)
        == Severity.SEVERE
    )
    assert (
        escalate_weather_severity(Severity.INFO, wind_gust_kmh=None, precipitation_mm=None, visibility_m=150)
        == Severity.SEVERE
    )


def test_align_exact_and_nearest():
    times = ["2026-09-20T00:00", "2026-09-20T01:00", "2026-09-20T02:00"]
    idx, t, exact = _align(times, datetime(2026, 9, 20, 1, 20, tzinfo=UTC))
    assert idx == 1 and exact
    idx, t, exact = _align(times, datetime(2026, 9, 20, 6, 0, tzinfo=UTC))
    assert idx is None  # > 3h away: refuse to interpolate silently
    idx, t, exact = _align(["2026-09-20T00:00"], datetime(2026, 9, 20, 2, 0, tzinfo=UTC))
    assert idx == 0 and not exact


@pytest.mark.asyncio
@respx.mock
async def test_weather_forecast_from_real_payload(fx):
    payload = fx("open_meteo_forecast.bkk_cnx.json")
    respx.get("https://wx.test/v1/forecast").mock(return_value=httpx.Response(200, json=payload))
    a = OpenMeteoWeatherAdapter("https://wx.test", service_name=SVC, ttl_hourly=3600, ttl_current=900)
    first_time = datetime.fromisoformat(payload[0]["hourly"]["time"][3]).replace(tzinfo=UTC)
    samples = [
        WeatherSample(index=0, point=Point(coordinates=[100.5018, 13.7563]), eta_at=first_time),
        WeatherSample(index=1, point=Point(coordinates=[98.9853, 18.7883]), eta_at=first_time + timedelta(hours=2)),
    ]
    out = await a.forecast_for_samples(samples)
    assert len(out) == 2
    p = out[0]
    assert p.valid_at == first_time and p.route_sample_index == 0
    assert p.source.provider == "open_meteo" and p.source.authority == SourceAuthority.LICENSED_PROVIDER
    assert p.source.observed_at is None  # forecast, not observation
    assert p.quality.completeness is not None
    # zero must be preserved as a real value, never turned into None
    assert isinstance(p.precipitation_mm, float)
    await a.close()


@pytest.mark.asyncio
async def test_weather_beyond_horizon_is_outside_coverage():
    a = OpenMeteoWeatherAdapter("https://wx.test", service_name=SVC, ttl_hourly=3600, ttl_current=900)
    with pytest.raises(ProviderError) as ei:
        await a.forecast_for_samples(
            [WeatherSample(index=0, point=Point(coordinates=[0, 0]), eta_at=NOW + timedelta(days=40))]
        )
    assert ei.value.code == ProviderErrorCode.OUTSIDE_COVERAGE
    await a.close()


# ------------------------------------------------------------------ disasters
@pytest.mark.asyncio
@respx.mock
async def test_usgs_real_payload(fx):
    respx.get("https://usgs.test/fdsnws/event/1/query").mock(
        return_value=httpx.Response(200, json=fx("usgs.southeast_asia.json"))
    )
    a = UsgsAdapter("https://usgs.test/fdsnws/event/1/query", service_name=SVC, ttl=600, min_magnitude=2.5)
    out = await a.query(BBox(min_lon=95, min_lat=5, max_lon=110, max_lat=22), lookback_days=30)
    assert out
    e = out[0]
    assert (
        e.event_type == DisasterEventType.EARTHQUAKE and e.official and e.source.authority == SourceAuthority.OFFICIAL
    )
    assert e.event_id.startswith("usgs:") and e.source.source_url.startswith("https://earthquake.usgs.gov/")
    assert e.effective_at is not None and e.source.observed_at == e.effective_at
    assert e.source.content_hash and len(e.source.content_hash) == 64
    await a.close()


@pytest.mark.asyncio
@respx.mock
async def test_usgs_schema_drift_raises():
    respx.get("https://usgs.test/fdsnws/event/1/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "features": [{"id": "x", "properties": {"time": 1}, "geometry": {"type": "Polygon", "coordinates": []}}]
            },
        )
    )
    a = UsgsAdapter("https://usgs.test/fdsnws/event/1/query", service_name=SVC, ttl=600, min_magnitude=2.5)
    with pytest.raises(ProviderError) as ei:
        await a.query(BBox(min_lon=-10, min_lat=-10, max_lon=10, max_lat=10), lookback_days=1)
    assert ei.value.code == ProviderErrorCode.PROVIDER_SCHEMA_CHANGED
    await a.close()


@pytest.mark.asyncio
@respx.mock
async def test_gdacs_real_payload_filters_bbox_and_maps_alert_level(fx):
    payload = fx("gdacs.eventlist.json")
    respx.get("https://gdacs.test/api/events/geteventlist/SEARCH").mock(return_value=httpx.Response(200, json=payload))
    a = GdacsAdapter("https://gdacs.test", service_name=SVC, ttl=600)
    world = BBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90)
    out = await a.query(world, lookback_days=7)
    assert len(out) == len(payload["features"])
    for e in out:
        assert e.source.attribution.startswith("Global Disaster Awareness")
        assert e.source.authority == SourceAuthority.INTERGOVERNMENTAL
        assert e.severity in (Severity.MINOR, Severity.SEVERE, Severity.EXTREME, Severity.UNKNOWN)
        assert e.official == (e.severity in (Severity.SEVERE, Severity.EXTREME))
    nowhere = BBox(min_lon=0, min_lat=-1, max_lon=0.1, max_lat=-0.9)
    assert await a.query(nowhere, lookback_days=7) == []
    await a.close()


@pytest.mark.asyncio
@respx.mock
async def test_eonet_real_payload(fx):
    respx.get("https://eonet.test/events").mock(return_value=httpx.Response(200, json=fx("eonet.open_events.json")))
    a = EonetAdapter("https://eonet.test", service_name=SVC, ttl=600)
    out = await a.query(BBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90), lookback_days=20)
    assert out
    for e in out:
        assert e.official is False  # curated metadata, not an official warning
        assert e.source.provider == "eonet" and e.source.source_url.startswith("https://eonet.gsfc.nasa.gov/")
        assert e.effective_at is not None and e.updated_at is not None and e.updated_at >= e.effective_at
    await a.close()


# ------------------------------------------------------------------ GTFS-RT
def _mbta_registry() -> GtfsRegistry:
    return GtfsRegistry(
        config_version="1.0.0",
        gtfs_feeds=[
            GtfsFeed(
                id="mbta",
                agency="MBTA",
                country_code="US",
                region="Boston",
                bbox=[-71.65, 41.95, -70.55, 42.75],
                timezone="America/New_York",
                modes=[TravelMode.TRAIN, TravelMode.BUS],
                static_url="https://mbta.test/MBTA_GTFS.zip",
                realtime={"alerts": "https://mbta.test/realtime/Alerts.pb"},
                license="MBTA developer license",
                attribution="MBTA",
            )
        ],
    )


@pytest.mark.asyncio
@respx.mock
async def test_gtfs_alerts_real_capture_marks_stale_when_old(fx_bytes):
    respx.get("https://mbta.test/realtime/Alerts.pb").mock(
        return_value=httpx.Response(200, content=fx_bytes("mbta.alerts.pb"))
    )
    a = GtfsRealtimeAdapter(_mbta_registry(), service_name=SVC, ttl=90, cache_dir="data/gtfs-cache-test")
    out = await a.status(
        Point(coordinates=[-71.0589, 42.3601]), Point(coordinates=[-71.1097, 42.3736]), TravelMode.TRAIN
    )
    assert out
    # the capture is older than the 90 s TTL by the time tests run: status must be UNKNOWN + STALE, never ON_TIME
    assert all(r.status.value == "UNKNOWN" for r in out)
    assert all(r.quality.status == DataStatus.STALE and QualityFlag.STALE in r.quality.flags for r in out)
    assert all(r.source.attribution == "MBTA" and r.source.authority == SourceAuthority.OFFICIAL for r in out)
    await a.close()


@pytest.mark.asyncio
async def test_gtfs_outside_coverage():
    a = GtfsRealtimeAdapter(_mbta_registry(), service_name=SVC, ttl=90, cache_dir="data/gtfs-cache-test")
    with pytest.raises(ProviderError) as ei:
        await a.status(Point(coordinates=[100.5, 13.7]), Point(coordinates=[98.9, 18.7]), TravelMode.TRAIN)
    assert ei.value.code == ProviderErrorCode.OUTSIDE_COVERAGE
    await a.close()


def test_gtfs_registry_loads_repo_config():
    reg = GtfsRegistry.load("config/providers.yaml")
    assert reg.gtfs_feeds and reg.gtfs_feeds[0].id == "mbta"
    assert reg.gtfs_feeds[0].license and reg.gtfs_feeds[0].attribution


# ------------------------------------------------------------------ ORS
@pytest.mark.asyncio
async def test_ors_not_configured_and_unsupported_mode():
    a = OpenRouteServiceAdapter("https://ors.test", "", service_name=SVC, ttl_route=3600, ttl_places=3600)
    assert a.descriptor.enabled is False
    with pytest.raises(ProviderError) as ei:
        await a.directions(Point(coordinates=[0, 0]), Point(coordinates=[1, 1]), TravelMode.CAR, departure_time=NOW)
    assert ei.value.code == ProviderErrorCode.NOT_CONFIGURED
    b = OpenRouteServiceAdapter("https://ors.test", "k", service_name=SVC, ttl_route=3600, ttl_places=3600)
    with pytest.raises(ProviderError) as ei2:
        await b.directions(Point(coordinates=[0, 0]), Point(coordinates=[1, 1]), TravelMode.FLIGHT, departure_time=NOW)
    assert ei2.value.code == ProviderErrorCode.OUTSIDE_COVERAGE
    await a.close()
    await b.close()


@pytest.mark.asyncio
@respx.mock
async def test_ors_directions_geojson_contract():
    body = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "summary": {"distance": 12000.0, "duration": 900.0},
                    "segments": [
                        {
                            "distance": 12000.0,
                            "duration": 900.0,
                            "steps": [{"distance": 1, "duration": 1, "instruction": "Head north"}],
                        }
                    ],
                    "way_points": [0, 2],
                },
                "geometry": {"type": "LineString", "coordinates": [[100.50, 13.75], [100.55, 13.80], [100.60, 13.85]]},
            }
        ],
    }
    route = respx.post("https://ors.test/v2/directions/driving-car/geojson").mock(
        return_value=httpx.Response(200, json=body)
    )
    a = OpenRouteServiceAdapter("https://ors.test", "secret-key", service_name=SVC, ttl_route=3600, ttl_places=3600)
    out = await a.directions(
        Point(coordinates=[100.50, 13.75]), Point(coordinates=[100.60, 13.85]), TravelMode.CAR, departure_time=NOW
    )
    assert route.calls[0].request.headers["Authorization"] == "secret-key"
    sent = route.calls[0].request.content
    assert b"alternative_routes" in sent  # short route: alternatives requested
    assert out[0].distance_m == 12000.0 and out[0].segments[0].instruction == "Head north"
    assert out[0].segments[0].eta_end == NOW + timedelta(seconds=900)
    await a.close()
