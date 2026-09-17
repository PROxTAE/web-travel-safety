"""Internal API — 00_API_AND_DATA_CONTRACTS §5.1. No chain-of-thought is ever exposed; only structured run state."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sta_common.context import get_context
from sta_common.envelope import ok
from sta_common.errors import AppError, ErrorCode
from sta_contracts.enums import RunStatus
from sta_contracts.models import AgentRunRequest, TravelRequest

from app.settings import Settings


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    travel_request: TravelRequest = Field(
        description="updated request with the missing fields filled or the follow-up question"
    )


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="user_cancelled", max_length=120)


def build_router(settings: Settings, auth_dep: Any) -> APIRouter:
    r = APIRouter(prefix="/internal/v1", dependencies=[Depends(auth_dep)], tags=["internal"])
    cv = settings.contract_version

    @r.post("/runs", status_code=202)
    async def create_run(body: AgentRunRequest, request: Request) -> dict[str, Any]:
        mgr = request.app.state.runs
        existing = await mgr.get(str(body.travel_request.request_id))
        if existing is not None:
            raise AppError(ErrorCode.CONFLICT, "run already exists for this request_id")
        ref = await mgr.start(body, correlation_id=get_context().correlation_id)
        return ok(ref.model_dump(mode="json"), cv)

    @r.get("/runs/{request_id}")
    async def get_run(request_id: UUID, request: Request) -> dict[str, Any]:
        st = await request.app.state.runs.get(str(request_id))
        if st is None:
            raise AppError(ErrorCode.NOT_FOUND, "run not found")
        return ok(st.model_dump(mode="json"), cv, degraded_services=st.degraded_services)

    @r.get("/runs/{request_id}/events")
    async def get_events(request_id: UUID, request: Request) -> dict[str, Any]:
        st = await request.app.state.runs.get(str(request_id))
        if st is None:
            raise AppError(ErrorCode.NOT_FOUND, "run not found")
        return ok(await request.app.state.progress.events(str(request_id)), cv)

    @r.post("/runs/{request_id}/resume", status_code=202)
    async def resume_run(request_id: UUID, body: ResumeRequest, request: Request) -> dict[str, Any]:
        mgr = request.app.state.runs
        st = await mgr.get(str(request_id))
        if st is None:
            raise AppError(ErrorCode.NOT_FOUND, "run not found")
        if st.status not in (RunStatus.NEEDS_INPUT, RunStatus.COMPLETED, RunStatus.PARTIAL):
            raise AppError(ErrorCode.CONFLICT, f"run in status {st.status.value} cannot be resumed")
        if body.travel_request.trip_id != st.trip_id:
            raise AppError(ErrorCode.FORBIDDEN, "resume must target the same trip")
        # a resume is a new run (new request_id) linked by conversation; it never mutates the old state
        if body.travel_request.request_id == request_id:
            raise AppError(ErrorCode.VALIDATION_ERROR, "resume requires a new request_id")
        new_req = body.travel_request.model_copy(update={"supersedes_request_id": request_id})
        ref = await mgr.start(
            AgentRunRequest(
                travel_request=new_req, conversation_id=st.conversation_id, resume_from_request_id=request_id
            ),
            correlation_id=get_context().correlation_id,
        )
        return ok(ref.model_dump(mode="json"), cv)

    @r.post("/runs/{request_id}/cancel")
    async def cancel_run(request_id: UUID, body: CancelRequest, request: Request) -> dict[str, Any]:
        st = await request.app.state.runs.cancel(str(request_id), body.reason)
        if st is None:
            raise AppError(ErrorCode.NOT_FOUND, "run not found")
        return ok({"request_id": str(request_id), "status": st.status.value}, cv)

    @r.get("/tools")
    async def tools(request: Request) -> dict[str, Any]:
        from app.tools.registry import TOOLS

        return ok(
            [
                {
                    "name": t.full_name,
                    "path": t.path,
                    "stages": [s.value for s in t.allowed_stages],
                    "timeout_seconds": t.timeout_seconds,
                    "max_attempts": t.max_attempts,
                    "idempotent": t.idempotent,
                }
                for t in TOOLS.values()
            ],
            cv,
        )

    return r
