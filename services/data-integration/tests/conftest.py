"""Shared builders. Records mimic the canonical output of คน 4 (values are plausible, not asserted as live data)."""

import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from sta_contracts.enums import DisasterEventType, RouteLabel, Severity, SourceAuthority, TravelMode
from sta_contracts.geo import LineString, Point
from sta_contracts.models import (
    DataQuality,
    DisasterEvent,
    ExternalContext,
    LocationRef,
    ProviderHealth,
    RouteCandidate,
    RouteSegment,
    SourceProvenance,
    TravelRequest,
    WeatherForecastPoint,
)

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SERVICE_AUTH_TOKEN", "")

NOW = datetime(2026, 9, 20, 2, 0, tzinfo=UTC)  # 09:00 Asia/Bangkok
BKK = [100.5018, 13.7563]
CNX = [98.9853, 18.7883]


def h(x: str) -> str:
    return hashlib.sha256(x.encode()).hexdigest()


def src(
    provider: str, rid: str, authority=SourceAuthority.OFFICIAL, observed=NOW, fetched=NOW, ttl=600
) -> SourceProvenance:
    return SourceProvenance(
        source_id=uuid5(NAMESPACE_URL, f"{provider}:{rid}"),
        provider=provider,
        provider_record_id=rid,
        authority=authority,
        source_url=f"https://{provider}.example/{rid}",
        license="test",
        observed_at=observed,
        fetched_at=fetched,
        expires_at=fetched + timedelta(seconds=ttl),
        content_hash=h(f"{provider}:{rid}"),
    )


def loc(coords, name, tz="Asia/Bangkok", cc="TH") -> LocationRef:
    return LocationRef(
        place_id=name,
        display_name=name,
        coordinates=Point(coordinates=coords),
        country_code=cc,
        timezone=tz,
        provider="open_meteo_geocoding",
        confirmed_by_user=True,
    )


def straight_route(
    a=BKK, b=CNX, mode=TravelMode.CAR, duration_s=8 * 3600, label=RouteLabel.ORIGINAL, inferred=False, n=6
) -> RouteCandidate:
    coords = [[a[0] + (b[0] - a[0]) * i / (n - 1), a[1] + (b[1] - a[1]) * i / (n - 1)] for i in range(n)]
    line = LineString(coordinates=coords)
    q = DataQuality(
        score=0.45 if inferred else 0.9, flags=["INFERRED", "OUTSIDE_COVERAGE"] if inferred else [], freshness_seconds=0
    )
    return RouteCandidate(
        route_id=uuid5(NAMESPACE_URL, f"route:{label}:{a}:{b}:{mode}"),
        label=label,
        mode=mode,
        geometry=line,
        segments=[
            RouteSegment(
                index=0,
                mode=mode,
                geometry=line,
                distance_m=580_000,
                duration_seconds=duration_s,
                eta_start=NOW,
                eta_end=NOW + timedelta(seconds=duration_s),
            )
        ],
        distance_m=580_000,
        duration_seconds=duration_s,
        quality=q,
        sources=[
            src("openrouteservice" if not inferred else "geodesic", f"r-{label}", SourceAuthority.LICENSED_PROVIDER)
        ],
    )


def wx(
    coords, valid_at, *, idx, severity=Severity.INFO, precip=0.0, gust=10.0, vis=20000.0, temp=30.0
) -> WeatherForecastPoint:
    return WeatherForecastPoint(
        id=uuid5(NAMESPACE_URL, f"wx:{idx}:{valid_at.isoformat()}"),
        location=Point(coordinates=coords),
        valid_at=valid_at,
        route_sample_index=idx,
        eta_at=valid_at,
        temperature_c=temp,
        precipitation_mm=precip,
        precipitation_probability=20.0,
        wind_gust_kmh=gust,
        visibility_m=vis,
        weather_code=61 if precip > 0 else 1,
        severity=severity,
        quality=DataQuality(score=0.9, completeness=1.0, freshness_seconds=120),
        source=src("open_meteo", f"wx-{idx}", SourceAuthority.LICENSED_PROVIDER),
    )


def event(
    eid,
    coords,
    etype=DisasterEventType.EARTHQUAKE,
    severity=Severity.MODERATE,
    official=True,
    closure=False,
    provider="usgs",
    effective=NOW - timedelta(hours=1),
    ends=None,
    magnitude=5.0,
    authority=SourceAuthority.OFFICIAL,
) -> DisasterEvent:
    return DisasterEvent(
        event_id=eid,
        event_type=etype,
        title=eid,
        severity=severity,
        magnitude=magnitude,
        geometry=Point(coordinates=coords),
        effective_at=effective,
        ends_at=ends,
        updated_at=effective,
        official=official,
        closure=closure,
        quality=DataQuality(score=0.9, freshness_seconds=60),
        source=src(provider, eid, authority),
    )


def travel_request(modes=(TravelMode.CAR,)) -> TravelRequest:
    return TravelRequest(
        request_id=uuid4(),
        trip_id=uuid4(),
        origin=loc(BKK, "Bangkok"),
        destination=loc(CNX, "Chiang Mai"),
        departure_time=NOW,
        travel_modes=list(modes),
        timezone="Asia/Bangkok",
    )


def context(
    req: TravelRequest, *, routes=None, weather=None, events=None, transport=None, degraded=None, unavailable=None
) -> ExternalContext:
    routes = routes if routes is not None else [straight_route()]
    if weather is None:
        r = routes[0]
        weather = [
            wx(c, NOW + timedelta(seconds=r.duration_seconds * i / (len(r.geometry.coordinates) - 1)), idx=i)
            for i, c in enumerate(r.geometry.coordinates)
        ]
    events = events or []
    return ExternalContext(
        context_id=uuid4(),
        request_id=req.request_id,
        routes=routes,
        weather=weather,
        transport=transport or [],
        disaster_events=events,
        official_alerts=[e for e in events if e.official],
        provider_health=[
            ProviderHealth(provider="open_meteo", kind="WEATHER", status="UP", enabled=True, checked_at=NOW)
        ],
        degraded_services=degraded or [],
        unavailable_capabilities=unavailable or [],
        fetched_at=NOW,
    )


@pytest.fixture
def settings():
    from app.settings import Settings

    return Settings(app_env="test")


@pytest.fixture
def schema(settings):
    from app.domain.features import load_schema

    return load_schema(settings.feature_schema_path)
