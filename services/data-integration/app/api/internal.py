"""Internal API — 00_API_AND_DATA_CONTRACTS §5.3"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sta_common.envelope import ok
from sta_common.errors import AppError, ErrorCode
from sta_contracts.enums import QualityGate
from sta_contracts.models import SnapshotCreateRequest

from app.pipeline.build_snapshot import build_snapshot, input_hash
from app.repositories.snapshot_repo import SnapshotRepository
from app.settings import Settings


class ValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strict: bool = False


class QualityReport(BaseModel):
    snapshot_id: UUID
    gate: QualityGate
    score: float
    reasons: list[str]
    weather_coverage: float | None
    conflicts: int
    safety_critical_conflicts: int
    degraded_services: list[str]
    age_seconds: int
    strict_ok: bool


def build_router(settings: Settings, auth_dep: Any) -> APIRouter:
    r = APIRouter(prefix="/internal/v1", dependencies=[Depends(auth_dep)], tags=["internal"])
    cv = settings.contract_version

    def repo(request: Request) -> SnapshotRepository:
        return request.app.state.repo  # type: ignore[no-any-return]

    def schema(request: Request) -> dict[str, Any]:
        return request.app.state.feature_schema  # type: ignore[no-any-return]

    @r.post("/snapshots", status_code=201)
    async def create_snapshot(body: SnapshotCreateRequest, request: Request) -> dict[str, Any]:
        req, ctx = body.travel_request, body.external_context
        ih = input_hash(req, ctx, settings)
        existing = await repo(request).find_existing(req.request_id, ih, settings.schema_version)
        if existing is not None:
            return ok(
                existing.model_dump(mode="json"), cv, degraded_services=existing.quality_summary.degraded_services
            )
        supersedes = await repo(request).latest_for_trip(req.trip_id)
        try:
            result = build_snapshot(
                req,
                ctx,
                settings,
                schema(request),
                supersedes=supersedes.snapshot_id if supersedes else None,
                corridor_buffer_m=body.corridor_buffer_m,
            )
        except ValueError as exc:
            raise AppError(ErrorCode.INSUFFICIENT_EVIDENCE, str(exc)) from None
        snap = await repo(request).persist(result)
        return ok(snap.model_dump(mode="json"), cv, degraded_services=snap.quality_summary.degraded_services)

    @r.get("/snapshots/{snapshot_id}")
    async def get_snapshot(snapshot_id: UUID, request: Request) -> dict[str, Any]:
        snap = await repo(request).get(snapshot_id)
        if snap is None:
            raise AppError(ErrorCode.NOT_FOUND, "snapshot not found")
        return ok(snap.model_dump(mode="json"), cv, degraded_services=snap.quality_summary.degraded_services)

    @r.post("/snapshots/{snapshot_id}/validate")
    async def validate_snapshot(snapshot_id: UUID, body: ValidateRequest, request: Request) -> dict[str, Any]:
        snap = await repo(request).get(snapshot_id)
        if snap is None:
            raise AppError(ErrorCode.NOT_FOUND, "snapshot not found")
        qs = snap.quality_summary
        age = int((datetime.now(UTC) - snap.created_at).total_seconds())
        strict_ok = qs.gate == QualityGate.PASS and age < settings.fresh_weather_s
        report = QualityReport(
            snapshot_id=snap.snapshot_id,
            gate=qs.gate,
            score=qs.overall.score,
            reasons=qs.reasons,
            weather_coverage=qs.weather.coverage,
            conflicts=snap.conflict_summary.count,
            safety_critical_conflicts=snap.conflict_summary.safety_critical,
            degraded_services=qs.degraded_services,
            age_seconds=age,
            strict_ok=strict_ok if body.strict else qs.gate != QualityGate.BLOCK,
        )
        return ok(report.model_dump(mode="json"), cv)

    @r.get("/events/search")
    async def events_search(
        request: Request,
        bbox: str = Query(..., description="min_lon,min_lat,max_lon,max_lat"),
        at: datetime | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> dict[str, Any]:
        from sta_contracts.geo import BBox

        try:
            b = BBox.parse(bbox)
        except ValueError as exc:
            raise AppError(ErrorCode.VALIDATION_ERROR, str(exc)) from None
        when = (at or datetime.now(UTC)).astimezone(UTC)
        rows = await repo(request).events_in_bbox(b.min_lon, b.min_lat, b.max_lon, b.max_lat, when, limit)
        return ok(rows, cv)

    @r.get("/feature-schema")
    async def feature_schema(request: Request) -> dict[str, Any]:
        return ok(schema(request), cv)

    return r


__all__ = ["build_router", "Field"]
