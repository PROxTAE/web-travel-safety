"""Feature schema v1 — deterministic raw feature values + missing indicators.

The exact same function (``build_features``) is used online (snapshot) and offline (training parity CLI),
so there is no semantic drift between inference and training. Encoding/imputation is NOT done here
(it lives in คน 6's versioned sklearn pipeline).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from statistics import fmean
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from sta_contracts.enums import (
    SEVERITY_RANK,
    DataStatus,
    DisasterEventType,
    QualityFlag,
    TransportServiceStatus,
    TravelMode,
)
from sta_contracts.models import DataQuality, DisasterEvent, RouteCandidate, TransportStatus, WeatherForecastPoint

from app.pipeline.enrich_geospatial import Corridor, EventHit, WeatherMatch, segments_hit_by_closures

FEATURE_SCHEMA_VERSION = "1.0.0"
MODE_CODE: dict[TravelMode, int] = {
    TravelMode.FLIGHT: 0,
    TravelMode.TRAIN: 1,
    TravelMode.BUS: 2,
    TravelMode.CAR: 3,
    TravelMode.WALK: 4,
    TravelMode.BICYCLE: 5,
    TravelMode.MULTIMODAL: 6,
}


def load_schema(path: str | Path) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert data["version"] == FEATURE_SCHEMA_VERSION, "feature schema version mismatch"
    return data  # type: ignore[no-any-return]


def feature_names(schema: dict[str, Any]) -> list[str]:
    return list(schema["features"].keys())


def _max(values: list[float | None]) -> float | None:
    vs = [v for v in values if v is not None]
    return max(vs) if vs else None


def _min(values: list[float | None]) -> float | None:
    vs = [v for v in values if v is not None]
    return min(vs) if vs else None


def build_features(
    *,
    route: RouteCandidate,
    corridor: Corridor,
    hits: list[EventHit],
    weather_matches: list[WeatherMatch],
    weather_coverage: float,
    transport: list[TransportStatus],
    all_qualities: list[DataQuality],
    conflict_count: int,
    departure_at: datetime,
    timezone: str,
    schema: dict[str, Any],
) -> dict[str, float | int | None]:
    active = [h for h in hits if h.inside_corridor and h.time_overlap]
    quakes = [h for h in hits if h.event.event_type == DisasterEventType.EARTHQUAKE]
    wx = [m.point for m in weather_matches]
    local_dep = departure_at.astimezone(ZoneInfo(timezone))
    fresh_transport = [t for t in transport if t.quality.status == DataStatus.FRESH]
    delayed = [
        t
        for t in transport
        if t.status
        in (TransportServiceStatus.DELAYED, TransportServiceStatus.DISRUPTED, TransportServiceStatus.CANCELLED)
    ]
    scores = [q.score for q in all_qualities] or [0.0]
    stale = [q for q in all_qualities if QualityFlag.STALE in q.flags or q.status == DataStatus.STALE]

    f: dict[str, float | int | None] = {
        "route_distance_km": round(route.distance_m / 1000, 3),
        "route_duration_hours": round(route.duration_seconds / 3600, 3),
        "travel_mode_encoded": MODE_CODE[route.mode],
        "route_geometry_inferred": 1 if QualityFlag.INFERRED in route.quality.flags else 0,
        "weather_max_precip_probability": _max([w.precipitation_probability for w in wx]),
        "weather_max_precip_mm": _max([w.precipitation_mm for w in wx]),
        "weather_max_wind_gust_kmh": _max([w.wind_gust_kmh for w in wx]),
        "weather_min_visibility_m": _min([w.visibility_m for w in wx]),
        "weather_max_temperature_c": _max([w.temperature_c for w in wx]),
        "weather_min_temperature_c": _min([w.temperature_c for w in wx]),
        "weather_max_severity_rank": _max([float(SEVERITY_RANK[w.severity]) for w in wx]) if wx else None,
        "severe_weather_exposure_minutes": round(
            sum(m.minutes_covered for m in weather_matches if SEVERITY_RANK[m.point.severity] >= 4), 1
        ),
        "weather_coverage_ratio": round(weather_coverage, 3),
        "earthquake_max_magnitude_near_corridor": _max(
            [h.event.magnitude for h in quakes if h.inside_corridor and h.time_overlap]
        ),
        "earthquake_min_distance_km": round(min(h.distance_to_route_m for h in quakes) / 1000, 1) if quakes else None,
        "active_official_alert_count": sum(1 for h in active if h.event.official),
        "active_disaster_event_count": len(active),
        "max_disaster_severity_rank": _max([float(SEVERITY_RANK[h.event.severity]) for h in active])
        if active
        else None,
        "official_closure_count": sum(1 for h in active if h.event.closure),
        "closed_segment_count": segments_hit_by_closures(corridor, route, hits),
        "delayed_segment_count": len(delayed),
        "max_delay_minutes": _max([float(t.delay_minutes) for t in transport if t.delay_minutes is not None]),
        "transport_realtime_coverage_ratio": round(len(fresh_transport) / len(transport), 3) if transport else 0.0,
        "source_quality_mean": round(fmean(scores), 3),
        "source_quality_min": round(min(scores), 3),
        "stale_source_ratio": round(len(stale) / len(all_qualities), 3) if all_qualities else 0.0,
        "conflict_count": conflict_count,
        "missing_critical_count": 0,
        "departure_hour_local": local_dep.hour,
        "month": local_dep.month,
        "route_region_h3_res3_count": len(corridor.h3_cells),
    }
    critical = schema.get("critical", [])
    f["missing_critical_count"] = sum(1 for k in critical if f.get(k) is None)
    # missing indicators for nullable features (versioned in schema)
    for name, spec in schema["features"].items():
        if spec.get("nullable"):
            f[f"{name}_missing"] = 1 if f.get(name) is None else 0
    # guard: never emit a feature the schema does not know (prevents silent drift)
    unknown = [k for k in f if k not in schema["features"] and not k.endswith("_missing")]
    if unknown:
        raise ValueError(f"features not in schema v{FEATURE_SCHEMA_VERSION}: {unknown}")
    return f


def weather_points_of(matches: list[WeatherMatch]) -> list[WeatherForecastPoint]:
    return [m.point for m in matches]


__all__ = ["build_features", "load_schema", "feature_names", "FEATURE_SCHEMA_VERSION", "MODE_CODE", "DisasterEvent"]
