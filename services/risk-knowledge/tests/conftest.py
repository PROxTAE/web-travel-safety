import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from sta_contracts.enums import (
    DataStatus,
    DisasterEventType,
    QualityGate,
    RouteLabel,
    Severity,
    SourceAuthority,
    TravelMode,
)
from sta_contracts.geo import LineString, Point
from sta_contracts.models import (
    ConflictSummary,
    DataQuality,
    DisasterEvent,
    IntegratedTravelContext,
    QualitySummary,
    RouteCandidate,
    RouteExposure,
    RouteSegment,
    SourceProvenance,
    TravelWindow,
)

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SERVICE_AUTH_TOKEN", "")
os.environ.setdefault("EMBEDDING_PROVIDER", "hashing")
os.environ.setdefault("FEATURE_SCHEMA_PATH", "config/feature_schema.yaml")

NOW = datetime(2026, 9, 20, 2, 0, tzinfo=UTC)
BKK = [100.5018, 13.7563]
CNX = [98.9853, 18.7883]


def _src(provider="usgs", rid="x", authority=SourceAuthority.OFFICIAL):
    return SourceProvenance(
        source_id=uuid5(NAMESPACE_URL, f"{provider}:{rid}"),
        provider=provider,
        provider_record_id=rid,
        authority=authority,
        source_url=f"https://{provider}.example/{rid}",
        observed_at=NOW,
        fetched_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        content_hash=hashlib.sha256(rid.encode()).hexdigest(),
    )


def route(
    label=RouteLabel.ORIGINAL,
    duration_h=8.0,
    closed=False,
    hazard_ids=(),
    severe_minutes=0.0,
    max_sev=Severity.UNKNOWN,
    transfers=0,
    rid=None,
):
    line = LineString(coordinates=[BKK, [(BKK[0] + CNX[0]) / 2, (BKK[1] + CNX[1]) / 2], CNX])
    return RouteCandidate(
        route_id=rid or uuid5(NAMESPACE_URL, f"r:{label}:{duration_h}:{closed}:{len(hazard_ids)}"),
        label=label,
        mode=TravelMode.CAR,
        geometry=line,
        segments=[
            RouteSegment(
                index=0, mode=TravelMode.CAR, geometry=line, distance_m=580_000, duration_seconds=duration_h * 3600
            )
        ],
        distance_m=580_000,
        duration_seconds=duration_h * 3600,
        transfers=transfers,
        exposure=RouteExposure(
            score=1.0 if closed else 0.1 * len(hazard_ids),
            hazard_event_ids=list(hazard_ids),
            closed=closed,
            severe_weather_minutes=severe_minutes,
            max_hazard_severity=max_sev,
        ),
        usable=not closed,
        quality=DataQuality(score=0.9),
        sources=[_src("openrouteservice", str(label))],
    )


def event(eid, severity=Severity.MODERATE, official=True, closure=False, etype=DisasterEventType.EARTHQUAKE):
    return DisasterEvent(
        event_id=eid,
        event_type=etype,
        title=eid,
        severity=severity,
        geometry=Point(coordinates=[(BKK[0] + CNX[0]) / 2, (BKK[1] + CNX[1]) / 2]),
        effective_at=NOW - timedelta(hours=1),
        official=official,
        closure=closure,
        quality=DataQuality(score=0.9),
        source=_src(
            "gdacs" if etype != DisasterEventType.EARTHQUAKE else "usgs",
            eid,
            SourceAuthority.INTERGOVERNMENTAL if etype != DisasterEventType.EARTHQUAKE else SourceAuthority.OFFICIAL,
        ),
    )


BASE_FEATURES = {
    "route_distance_km": 580.0,
    "route_duration_hours": 8.0,
    "travel_mode_encoded": 3,
    "route_geometry_inferred": 0,
    "weather_max_precip_probability": 20.0,
    "weather_max_precip_mm": 0.5,
    "weather_max_wind_gust_kmh": 20.0,
    "weather_min_visibility_m": 20000.0,
    "weather_max_temperature_c": 31.0,
    "weather_min_temperature_c": 24.0,
    "weather_max_severity_rank": 1.0,
    "severe_weather_exposure_minutes": 0.0,
    "weather_coverage_ratio": 1.0,
    "earthquake_max_magnitude_near_corridor": None,
    "earthquake_min_distance_km": None,
    "active_official_alert_count": 0,
    "active_disaster_event_count": 0,
    "max_disaster_severity_rank": None,
    "official_closure_count": 0,
    "closed_segment_count": 0,
    "delayed_segment_count": 0,
    "max_delay_minutes": None,
    "transport_realtime_coverage_ratio": 0.0,
    "source_quality_mean": 0.9,
    "source_quality_min": 0.9,
    "stale_source_ratio": 0.0,
    "conflict_count": 0,
    "missing_critical_count": 0,
    "departure_hour_local": 9,
    "month": 9,
    "route_region_h3_res3_count": 3,
}
for k, v in list(BASE_FEATURES.items()):
    if k in (
        "weather_max_precip_probability",
        "weather_max_precip_mm",
        "weather_max_wind_gust_kmh",
        "weather_min_visibility_m",
        "weather_max_temperature_c",
        "weather_min_temperature_c",
        "weather_max_severity_rank",
        "earthquake_max_magnitude_near_corridor",
        "earthquake_min_distance_km",
        "max_disaster_severity_rank",
        "max_delay_minutes",
    ):
        BASE_FEATURES[f"{k}_missing"] = 1 if v is None else 0


def snapshot(
    routes=None,
    events=None,
    features=None,
    features_delayed=None,
    gate=QualityGate.PASS,
    status=DataStatus.FRESH,
    safety_conflicts=0,
):
    routes = routes or [route()]
    events = events or []
    f = {**BASE_FEATURES, **(features or {})}
    q = DataQuality(
        status=status,
        score=0.9 if gate == QualityGate.PASS else 0.5,
        coverage=f["weather_coverage_ratio"],
        completeness=0.9,
        freshness_seconds=60,
    )
    return IntegratedTravelContext(
        snapshot_id=uuid4(),
        request_id=uuid4(),
        trip_id=uuid4(),
        travel_window=TravelWindow(departure_at=NOW, arrival_at=NOW + timedelta(hours=8), timezone="Asia/Bangkok"),
        travel_modes=[TravelMode.CAR],
        route_candidates=routes,
        weather=[],
        transport=[],
        disaster_events=events,
        official_alerts=[e for e in events if e.official],
        features=f,
        features_delayed={**f, **(features_delayed or {})} if features_delayed is not None else None,
        delay_probe_minutes=360 if features_delayed is not None else None,
        quality_summary=QualitySummary(
            gate=gate,
            overall=q,
            weather=q,
            transport=q,
            disaster=q,
            route=q,
            reasons=[] if gate == QualityGate.PASS else ["X"],
        ),
        conflict_summary=ConflictSummary(count=safety_conflicts, safety_critical=safety_conflicts),
        source_ids=[],
        created_at=NOW,
        content_hash="a" * 64,
    )


@pytest.fixture
def settings():
    from app.settings import Settings

    return Settings(
        app_env="test",
        embedding_provider="hashing",
        feature_schema_path="config/feature_schema.yaml",
        risk_model_artifact_dir="tests/no-artifacts",
    )


@pytest.fixture
def thresholds():
    from app.risk.thresholds import load_thresholds

    return load_thresholds("config/thresholds.yaml")


@pytest.fixture
def overrides():
    from app.risk.thresholds import load_overrides

    return load_overrides("config/overrides.yaml")
