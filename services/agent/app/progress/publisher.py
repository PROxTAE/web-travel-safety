"""Progress events (SSE contract §6) published to a Redis Stream per run + a run-state key.

Stream ids are monotonic so the API can replay with Last-Event-ID. Events never contain prompts, raw provider
bodies, coordinates or tokens. In test mode everything stays in memory.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sta_common.logging import get_logger
from sta_contracts.enums import RunStage, RunStatus
from sta_contracts.models import RunProgressEvent, RunState

log = get_logger("progress")

STAGE_PERCENT = {
    RunStage.VALIDATING: 5,
    RunStage.FETCHING_EXTERNAL_DATA: 20,
    RunStage.INTEGRATING_DATA: 40,
    RunStage.ASSESSING_RISK: 55,
    RunStage.RETRIEVING_GUIDANCE: 62,
    RunStage.EVALUATING_ROUTES: 68,
    RunStage.MAKING_DECISION: 80,
    RunStage.EXPLAINING: 88,
    RunStage.FORMATTING_RESPONSE: 95,
}


class ProgressPublisher:
    def __init__(self, redis: Redis | None, env: str, state_ttl: int) -> None:
        self.redis = redis
        self.env = env
        self.state_ttl = state_ttl
        self.memory: dict[str, list[dict[str, Any]]] = {}
        self.states: dict[str, dict[str, Any]] = {}
        self._counters: dict[str, int] = {}

    def _stream(self, request_id: str) -> str:
        return f"sta:{self.env}:run:{request_id}:events"

    def _state_key(self, request_id: str) -> str:
        return f"sta:{self.env}:run:{request_id}:status"

    async def publish(self, event: RunProgressEvent) -> None:
        rid = str(event.request_id)
        payload = json.loads(event.model_dump_json(exclude_none=True))
        if self.redis is None:
            self.memory.setdefault(rid, []).append(payload)
            return
        try:
            await self.redis.xadd(self._stream(rid), {"data": json.dumps(payload)}, maxlen=500, approximate=True)
            await self.redis.expire(self._stream(rid), self.state_ttl)
        except Exception as exc:  # noqa: BLE001 - progress loss must not fail the run
            log.warning("progress_publish_failed", error_type=type(exc).__name__)

    def next_id(self, request_id: str) -> int:
        self._counters[request_id] = self._counters.get(request_id, 0) + 1
        return self._counters[request_id]

    async def stage(self, request_id: UUID, stage: RunStage, message_key: str | None = None) -> None:
        await self.publish(
            RunProgressEvent(
                event_id=self.next_id(str(request_id)),
                event_type="run.progress",
                request_id=request_id,
                status=RunStatus.RUNNING,
                stage=stage,
                percent=STAGE_PERCENT[stage],
                message_key=message_key or f"stage.{stage.value.lower()}",
                occurred_at=datetime.now(UTC),
            )
        )

    async def degraded(self, request_id: UUID, service: str, reason: str, retrying: bool = False) -> None:
        await self.publish(
            RunProgressEvent(
                event_id=self.next_id(str(request_id)),
                event_type="run.degraded",
                request_id=request_id,
                service=service,
                reason=reason[:200],
                retrying=retrying,
                occurred_at=datetime.now(UTC),
            )
        )

    async def accepted(self, request_id: UUID) -> None:
        await self.publish(
            RunProgressEvent(
                event_id=self.next_id(str(request_id)),
                event_type="run.accepted",
                request_id=request_id,
                status=RunStatus.QUEUED,
                occurred_at=datetime.now(UTC),
            )
        )

    async def needs_input(self, request_id: UUID, missing: list[str]) -> None:
        await self.publish(
            RunProgressEvent(
                event_id=self.next_id(str(request_id)),
                event_type="run.needs_input",
                request_id=request_id,
                status=RunStatus.NEEDS_INPUT,
                missing_fields=missing,
                prompt_key="needs_input.confirm_locations",
                occurred_at=datetime.now(UTC),
            )
        )

    async def completed(self, request_id: UUID, recommendation_id: UUID, status: RunStatus) -> None:
        await self.publish(
            RunProgressEvent(
                event_id=self.next_id(str(request_id)),
                event_type="run.completed",
                request_id=request_id,
                status=status,
                percent=100,
                recommendation_id=recommendation_id,
                result_url=f"/api/v1/recommendations/{recommendation_id}",
                occurred_at=datetime.now(UTC),
            )
        )

    async def failed(
        self, request_id: UUID, code: str, message: str, retryable: bool, status: RunStatus = RunStatus.FAILED
    ) -> None:
        await self.publish(
            RunProgressEvent(
                event_id=self.next_id(str(request_id)),
                event_type="run.failed",
                request_id=request_id,
                status=status,
                error_code=code,
                error_message=message[:200],
                retryable=retryable,
                occurred_at=datetime.now(UTC),
            )
        )

    async def save_state(self, state: RunState) -> None:
        rid = str(state.request_id)
        payload = json.loads(state.model_dump_json())
        if self.redis is None:
            self.states[rid] = payload
            return
        try:
            await self.redis.set(self._state_key(rid), json.dumps(payload), ex=self.state_ttl)
        except Exception as exc:  # noqa: BLE001
            log.warning("state_save_failed", error_type=type(exc).__name__)

    async def load_state(self, request_id: str) -> RunState | None:
        if self.redis is None:
            raw = self.states.get(request_id)
            return RunState.model_validate(raw) if raw else None
        raw = await self.redis.get(self._state_key(request_id))
        return RunState.model_validate(json.loads(raw)) if raw else None

    async def is_cancelled(self, request_id: str) -> bool:
        if self.redis is None:
            return bool(self.states.get(request_id, {}).get("_cancel"))
        return bool(await self.redis.exists(f"sta:{self.env}:run:{request_id}:cancel"))

    async def cancel(self, request_id: str) -> None:
        if self.redis is None:
            self.states.setdefault(request_id, {})["_cancel"] = True
            return
        await self.redis.set(f"sta:{self.env}:run:{request_id}:cancel", "1", ex=self.state_ttl)

    async def events(self, request_id: str) -> list[dict[str, Any]]:
        if self.redis is None:
            return list(self.memory.get(request_id, []))
        rows = await self.redis.xrange(self._stream(request_id))
        return [json.loads(v[b"data"]) for _id, v in rows]
