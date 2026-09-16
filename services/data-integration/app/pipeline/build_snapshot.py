"""Stage 7 — orchestrate validate → normalize → dedup/conflicts → corridor → features → quality → snapshot.

Deterministic: same input + versions ⇒ same ``content_hash``. ``created_at``/``snapshot_id`` are
excluded from the hash. The snapshot is immutable once persisted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sta_contracts.enums import DataStatus, QualityFlag, RouteLabel
from sta_contracts.models import (
    ConflictSummary,
    DataQuality,
    ExternalContext,
    IntegratedTravelContext,
    QualitySummary,
    RouteCandidate,
    TravelRequest,
    TravelWindow,
)

from app.domain.features import build_features
from app.domain.quality import overall_and_gate, summarize_group
from app.pipeline.deduplicate import deduplicate
from app.pipeline.enrich_geospatial import (
    EventHit,
    buffer_for_mode,
    build_corridor,
    exposure_for_route,
    intersect_events,
    match_weather,
)
from app.pipeline.normalize import LineageRow, normalize
from app.pipeline.validate import QuarantineItem, validate_context
from app.settings import Settings


@dataclass(slots=True)
class BuildResult:
    snapshot: IntegratedTravelContext
    lineage: list[LineageRow]
    quarantine: list[QuarantineItem]
    input_content_hash: str
    event_hits: list[EventHit] = field(default_factory=list)


def input_hash(req: TravelRequest, ctx: ExternalContext, s: Settings) -> str:
    payload = {
        "request": req.model_dump(mode="json", exclude={"request_id"}),
        "context": ctx.model_dump(mode="json", exclude={"context_id", "fetched_at", "provider_health"}),
        "schema_version": s.schema_version,
        "feature_schema_version": s.feature_schema_version,
        "transform_version": s.transform_version,
        "quality_weights_version": s.quality_weights_version,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _content_hash(snapshot_fields: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(snapshot_fields, sort_keys=True, default=str).encode()).hexdigest()


def build_snapshot(
    req: TravelRequest,
    ctx: ExternalContext,
    s: Settings,
    schema: dict[str, Any],
    *,
    supersedes: UUID | None = None,
    corridor_buffer_m: float | None = None,
    now: datetime | None = None,
) -> BuildResult:
    now = now or datetime.now(UTC)
    validated = validate_context(ctx, request_id=str(req.request_id), trip_id=str(req.trip_id))
    validated, lineage = normalize(validated)
    dedup = deduplicate(validated.events)
    events = dedup.events
    official = [e for e in events if e.official]

    routes = sorted(validated.routes, key=lambda r: (r.label != RouteLabel.ORIGINAL, r.duration_seconds))
    if not routes:
        raise ValueError("no valid route candidates in external context")
    primary = routes[0]
    arrival = req.departure_time + timedelta(seconds=primary.duration_seconds)
    window = TravelWindow(departure_at=req.departure_time, arrival_at=arrival, timezone=req.timezone)
    buffer_m = corridor_buffer_m or buffer_for_mode(
        req.travel_modes, ground_m=s.corridor_buffer_m_ground, flight_m=s.corridor_buffer_m_flight
    )

    all_hits: list[EventHit] = []
    primary_matches = None
    primary_coverage = 0.0
    primary_corridor = None
    for r in routes:
        corridor = build_corridor(r, buffer_m=buffer_m, densify_m=s.corridor_densify_m)
        hits = intersect_events(corridor, events, window)
        matches, coverage = match_weather(
            corridor,
            r,
            validated.weather,
            departure_at=req.departure_time,
            max_distance_m=s.weather_match_max_distance_m,
            tolerance_s=s.weather_match_tolerance_seconds,
            expected_spacing_m=s.weather_expected_spacing_m,
            expected_samples_max=s.weather_expected_samples_max,
        )
        r.exposure = exposure_for_route(corridor, r, hits, matches)
        r.usable = not r.exposure.closed
        if r.exposure.closed:
            r.quality.notes.append("route intersects an official closure; marked unusable")
        if r is primary:
            all_hits = hits
            primary_matches = matches
            primary_coverage = coverage
            primary_corridor = corridor
    assert primary_corridor is not None and primary_matches is not None

    all_q: list[DataQuality] = (
        [r.quality for r in routes]
        + [w.quality for w in validated.weather]
        + [t.quality for t in validated.transport]
        + [e.quality for e in events]
    )
    features = build_features(
        route=primary,
        corridor=primary_corridor,
        hits=all_hits,
        weather_matches=primary_matches,
        weather_coverage=primary_coverage,
        transport=validated.transport,
        all_qualities=all_q,
        conflict_count=len(dedup.conflicts),
        departure_at=req.departure_time,
        timezone=req.timezone,
        schema=schema,
    )

    route_q = summarize_group([r.quality for r in routes], ttl=s.fresh_route_s, coverage=None, notes=[])
    weather_q = summarize_group(
        [w.quality for w in validated.weather if w.probe == "PLANNED"],
        ttl=s.fresh_weather_s,
        coverage=primary_coverage,
        notes=[],
    )
    transport_q = summarize_group(
        [t.quality for t in validated.transport], ttl=s.fresh_transport_s, coverage=None, notes=[]
    )
    disaster_q = (
        summarize_group([e.quality for e in events], ttl=s.fresh_disaster_s, coverage=1.0, notes=[])
        if events
        else DataQuality(
            status=DataStatus.FRESH,
            score=1.0,
            coverage=1.0,
            completeness=1.0,
            notes=["no events in bbox/time window"],
            weights_version="1.0.0",
        )
    )
    if ctx.degraded_services:
        for name in ctx.degraded_services:
            if any(p in name for p in ("usgs", "gdacs", "eonet")):
                disaster_q.flags = sorted({*disaster_q.flags, QualityFlag.INCOMPLETE}, key=lambda f: f.value)
                disaster_q.notes.append(f"provider degraded: {name}")
                disaster_q.score = round(disaster_q.score * 0.8, 3)
                if disaster_q.status == DataStatus.FRESH:
                    disaster_q.status = DataStatus.PARTIAL
    overall, gate, reasons = overall_and_gate(
        s,
        route_q=route_q,
        weather_q=weather_q,
        transport_q=transport_q,
        disaster_q=disaster_q,
        routes=routes,
        weather_coverage=primary_coverage,
        conflicts_safety_critical=dedup.safety_critical_conflicts,
        official_alerts=official,
        now=now,
    )
    quality_summary = QualitySummary(
        gate=gate,
        overall=overall,
        weather=weather_q,
        transport=transport_q,
        disaster=disaster_q,
        route=route_q,
        degraded_services=list(ctx.degraded_services) + list(ctx.unavailable_capabilities),
        reasons=reasons,
    )
    conflict_summary = ConflictSummary(
        count=len(dedup.conflicts), safety_critical=dedup.safety_critical_conflicts, conflicts=dedup.conflicts
    )
    source_ids = sorted(
        {r_src.source_id for r in routes for r_src in r.sources}
        | {w.source.source_id for w in validated.weather}
        | {t.source.source_id for t in validated.transport}
        | {e.source.source_id for e in events},
        key=str,
    )
    hashed_fields = {
        "request_id": str(req.request_id),
        "trip_id": str(req.trip_id),
        "trip_revision": req.trip_revision,
        "schema_version": s.schema_version,
        "feature_schema_version": s.feature_schema_version,
        "transform_version": s.transform_version,
        "travel_window": window.model_dump(mode="json"),
        "routes": [r.model_dump(mode="json") for r in routes],
        "weather": [w.model_dump(mode="json") for w in validated.weather],
        "transport": [t.model_dump(mode="json") for t in validated.transport],
        "events": [e.model_dump(mode="json") for e in events],
        "features": features,
        "quality": quality_summary.model_dump(mode="json"),
        "conflicts": conflict_summary.model_dump(mode="json"),
        "corridor": primary_corridor.polygon_wgs.model_dump(mode="json"),
    }
    snapshot = IntegratedTravelContext(
        snapshot_id=uuid4(),
        request_id=req.request_id,
        trip_id=req.trip_id,
        trip_revision=req.trip_revision,
        schema_version=s.schema_version,
        feature_schema_version=s.feature_schema_version,
        transform_version=s.transform_version,
        travel_window=window,
        travel_modes=req.travel_modes,
        route_candidates=routes,
        route_corridor_geojson=primary_corridor.polygon_wgs,
        corridor_buffer_m=buffer_m,
        weather=validated.weather,
        transport=validated.transport,
        disaster_events=events,
        official_alerts=official,
        features=features,
        quality_summary=quality_summary,
        conflict_summary=conflict_summary,
        source_ids=source_ids,
        provider_health={h.provider: h.status for h in ctx.provider_health},
        created_at=now,
        content_hash=_content_hash(hashed_fields),
        supersedes_snapshot_id=supersedes,
    )
    return BuildResult(
        snapshot=snapshot,
        lineage=lineage,
        quarantine=validated.quarantine,
        input_content_hash=input_hash(req, ctx, s),
        event_hits=all_hits,
    )


__all__ = ["build_snapshot", "BuildResult", "input_hash", "RouteCandidate"]
