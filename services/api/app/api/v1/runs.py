"""/runs/{id} (poll), /runs/{id}/events (SSE), /runs/{id}/cancel, /runs/{id}/resume."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from sta_common.envelope import ok
from sta_common.errors import AppError, ErrorCode

from app.api.schemas import CancelBody
from app.application.assessments import StartParams
from app.application.sse import ConnectionGuard, EventBridge
from app.auth.deps import CurrentUser
from app.middleware.rate_limit import user_limit
from app.settings import Settings

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-store",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def build_router(settings: Settings) -> APIRouter:
    r = APIRouter(prefix="/api/v1", tags=["runs"])
    cv = settings.contract_version
    limited = user_limit("default", "rate_limit_default_per_minute")
    assess_limited = user_limit("assessment", "rate_limit_assessment_per_minute")

    @r.get("/runs/{request_id}")
    async def get_run(request_id: UUID, request: Request, user: CurrentUser = Depends(limited)) -> dict[str, Any]:
        st = await request.app.state.assessments.state(user, request_id)
        return ok(st, cv, degraded_services=st.get("degraded_services") or [])

    @r.get("/runs/{request_id}/events")
    async def run_events(
        request_id: UUID,
        request: Request,
        user: CurrentUser = Depends(limited),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        # authorize the owner before opening the stream; the row lookup is owner-scoped
        row = await request.app.state.repo.requests.get(user.id, request_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "run not found")
        guard: ConnectionGuard = request.app.state.sse_guard
        if not guard.acquire(str(user.id)):
            raise AppError(ErrorCode.RATE_LIMITED, "too many open event streams", retry_after_seconds=5)
        bridge: EventBridge = request.app.state.events
        last = int(last_event_id) if last_event_id and last_event_id.isdigit() else None

        async def gen() -> Any:
            try:
                async for chunk in bridge.stream(request_id, last):
                    yield chunk
            finally:
                guard.release(str(user.id))

        return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)

    @r.post("/runs/{request_id}/cancel")
    async def cancel_run(
        request_id: UUID, request: Request, body: CancelBody | None = None, user: CurrentUser = Depends(limited)
    ) -> dict[str, Any]:
        st = await request.app.state.assessments.cancel(user, request_id, (body or CancelBody()).reason)
        return ok(st, cv)

    @r.post("/runs/{request_id}/resume", status_code=202)
    async def resume_run(
        request_id: UUID,
        request: Request,
        user: CurrentUser = Depends(assess_limited),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
    ) -> dict[str, Any]:
        """After NEEDS_INPUT the client fixes the trip (PATCH /trips) and resumes: a new run linked to the old one."""
        st = request.app.state
        row = await st.repo.requests.get(user.id, request_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "run not found")
        if row["status"] != "NEEDS_INPUT":
            raise AppError(ErrorCode.CONFLICT, f"run in status {row['status']} cannot be resumed")
        trip = await st.repo.trips.get(user.id, row["trip_id"])
        if trip is None:
            raise AppError(ErrorCode.NOT_FOUND, "trip not found")
        result = await st.assessments.start(
            user,
            StartParams(
                trip=trip,
                idempotency_key=f"resume:{request_id}:{idempotency_key}" if idempotency_key else None,
                conversation_id=row.get("conversation_id"),
                resume_from=request_id,
                previous_recommendation_id=trip.latest_recommendation_id,
            ),
        )
        return ok(result.ref.model_dump(mode="json"), cv)

    return r
