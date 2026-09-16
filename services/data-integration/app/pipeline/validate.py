"""Stage 1 — validate. Invalid records go to quarantine with reason + content hash; nothing is dropped silently."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

from shapely.geometry import shape
from shapely.validation import explain_validity
from sta_contracts.models import DisasterEvent, ExternalContext, RouteCandidate, TransportStatus, WeatherForecastPoint


@dataclass(slots=True)
class QuarantineItem:
    record_type: str
    record_id: str
    error_code: str
    field_path: str
    content_hash: str
    provider: str


@dataclass(slots=True)
class ValidatedContext:
    routes: list[RouteCandidate]
    weather: list[WeatherForecastPoint]
    transport: list[TransportStatus]
    events: list[DisasterEvent]
    quarantine: list[QuarantineItem] = field(default_factory=list)


def _hash(model: Any) -> str:
    return hashlib.sha256(model.model_dump_json().encode()).hexdigest()


def _finite(*values: float | None) -> str | None:
    for v in values:
        if v is not None and (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            return "NON_FINITE_VALUE"
    return None


def _geometry_error(geom: Any) -> str | None:
    try:
        g = shape(geom.model_dump())
    except Exception:  # noqa: BLE001
        return "INVALID_GEOMETRY"
    if g.is_empty:
        return "EMPTY_GEOMETRY"
    if not g.is_valid:
        return "INVALID_GEOMETRY:" + str(explain_validity(g)).split("[")[0].strip()
    return None


def validate_context(ctx: ExternalContext, *, request_id: str, trip_id: str) -> ValidatedContext:
    out = ValidatedContext(routes=[], weather=[], transport=[], events=[])
    if str(ctx.request_id) != str(request_id):
        raise ValueError("external context request_id does not match travel request")

    for r in ctx.routes:
        err = _geometry_error(r.geometry) or _finite(r.distance_m, r.duration_seconds)
        if err is None and r.duration_seconds <= 0:
            err = "NON_POSITIVE_DURATION"
        if err:
            out.quarantine.append(
                QuarantineItem(
                    "route", str(r.route_id), err, "geometry", _hash(r), r.sources[0].provider if r.sources else "?"
                )
            )
            continue
        out.routes.append(r)

    for w in ctx.weather:
        err = _finite(
            w.temperature_c, w.precipitation_mm, w.wind_gust_kmh, w.visibility_m, w.wind_speed_kmh
        ) or _geometry_error(w.location)
        if err is None and w.precipitation_mm is not None and w.precipitation_mm < 0:
            err = "NEGATIVE_PRECIPITATION"
        if err is None and w.valid_at is None:
            err = "MISSING_VALID_AT"
        if err:
            out.quarantine.append(QuarantineItem("weather", str(w.id), err, "values", _hash(w), w.source.provider))
            continue
        out.weather.append(w)

    for t in ctx.transport:
        err = None
        if t.delay_minutes is not None and (t.delay_minutes < -60 or t.delay_minutes > 24 * 60):
            err = "IMPLAUSIBLE_DELAY"
        if t.scheduled_departure and t.scheduled_arrival and t.scheduled_arrival < t.scheduled_departure:
            err = "ARRIVAL_BEFORE_DEPARTURE"
        if err:
            out.quarantine.append(QuarantineItem("transport", str(t.id), err, "times", _hash(t), t.source.provider))
            continue
        out.transport.append(t)

    for e in ctx.disaster_events:
        err = _geometry_error(e.geometry) or _finite(e.magnitude)
        if err is None and e.effective_at and e.ends_at and e.ends_at < e.effective_at:
            err = "ENDS_BEFORE_EFFECTIVE"
        if err:
            out.quarantine.append(QuarantineItem("event", e.event_id, err, "geometry", _hash(e), e.source.provider))
            continue
        out.events.append(e)
    return out


def content_hash_of(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()
