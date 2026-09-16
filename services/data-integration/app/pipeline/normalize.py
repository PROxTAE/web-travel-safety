"""Stage 2 — normalize. Inputs are already canonical (คน 4), so this stage enforces invariants
rather than converting units: UTC timestamps, finite numbers, severity enum, safe geometry repair with
transform lineage. Every touched field yields a lineage row (record, field_path, source_id, transform).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from shapely.geometry import mapping, shape
from shapely.validation import make_valid
from sta_contracts.enums import Severity
from sta_contracts.geo import MultiPolygon, Point, Polygon
from sta_contracts.models import DisasterEvent, RouteCandidate, TransportStatus, WeatherForecastPoint

from app.pipeline.validate import ValidatedContext

TRANSFORM_VERSION = "1.0.0"


@dataclass(slots=True)
class LineageRow:
    record_type: str
    record_id: str
    field_path: str
    source_id: str
    transform: str
    transform_version: str = TRANSFORM_VERSION


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _normalize_times(
    model: Any, fields: tuple[str, ...], record_type: str, record_id: str, source_id: str, lineage: list[LineageRow]
) -> None:
    for f in fields:
        v = getattr(model, f, None)
        if isinstance(v, datetime):
            nv = _utc(v)
            if nv != v or v.tzinfo is not UTC:
                setattr(model, f, nv)
                lineage.append(LineageRow(record_type, record_id, f, source_id, "to_utc"))


def normalize(v: ValidatedContext) -> tuple[ValidatedContext, list[LineageRow]]:
    lineage: list[LineageRow] = []
    for r in v.routes:
        sid = str(r.sources[0].source_id) if r.sources else "unknown"
        for seg in r.segments:
            _normalize_times(seg, ("eta_start", "eta_end"), "route", str(r.route_id), sid, lineage)
        _normalize_route(r, sid, lineage)
    for w in v.weather:
        _normalize_times(w, ("valid_at", "eta_at"), "weather", str(w.id), str(w.source.source_id), lineage)
        if w.severity is Severity.UNKNOWN and w.weather_code is not None:
            lineage.append(
                LineageRow("weather", str(w.id), "severity", str(w.source.source_id), "severity_unknown_kept")
            )
    for t in v.transport:
        _normalize_times(
            t,
            ("scheduled_departure", "estimated_departure", "scheduled_arrival", "estimated_arrival"),
            "transport",
            str(t.id),
            str(t.source.source_id),
            lineage,
        )
    for e in v.events:
        _normalize_times(
            e, ("effective_at", "ends_at", "updated_at"), "event", e.event_id, str(e.source.source_id), lineage
        )
        _repair_event_geometry(e, lineage)
    return v, lineage


def _normalize_route(r: RouteCandidate, sid: str, lineage: list[LineageRow]) -> None:
    # drop consecutive duplicate vertices (safe, topology preserving)
    coords = r.geometry.coordinates
    deduped = [coords[0]]
    for c in coords[1:]:
        if c[:2] != deduped[-1][:2]:
            deduped.append(c)
    if len(deduped) != len(coords) and len(deduped) >= 2:
        r.geometry.coordinates = deduped
        lineage.append(LineageRow("route", str(r.route_id), "geometry", sid, "drop_duplicate_vertices"))


def _repair_event_geometry(e: DisasterEvent, lineage: list[LineageRow]) -> None:
    if isinstance(e.geometry, Point):
        return
    g = shape(e.geometry.model_dump())
    if g.is_valid:
        return
    fixed = make_valid(g)
    if fixed.geom_type == "Polygon":
        e.geometry = Polygon.model_validate(mapping(fixed))
    elif fixed.geom_type == "MultiPolygon":
        e.geometry = MultiPolygon.model_validate(mapping(fixed))
    else:
        return
    lineage.append(LineageRow("event", e.event_id, "geometry", str(e.source.source_id), "make_valid"))
    e.quality.notes.append("geometry repaired with make_valid (topology may differ from source)")


__all__ = ["LineageRow", "normalize", "TRANSFORM_VERSION", "TransportStatus", "WeatherForecastPoint"]
