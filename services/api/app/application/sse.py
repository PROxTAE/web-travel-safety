"""SSE bridge (00_API_AND_DATA_CONTRACTS §6).

Source of truth is the agent's Redis stream ``sta:{env}:run:{id}:events`` (blocking XREAD); without Redis (tests) the
agent's ``GET /runs/{id}/events`` is polled. Every payload is re-validated as ``RunProgressEvent`` and re-serialized
from the contract model, so nothing outside the contract (provider bodies, prompts, coordinates) can pass through.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from redis.asyncio import Redis
from sta_common.logging import get_logger
from sta_contracts.models import RunProgressEvent

from app.application.assessments import result_url
from app.clients.downstream import AgentClient
from app.settings import Settings

log = get_logger("sse")
TERMINAL_EVENTS = {"run.completed", "run.failed"}


def format_event(ev: RunProgressEvent) -> str:
    data = ev.model_dump(mode="json", exclude_none=True)
    if ev.recommendation_id and not ev.result_url:
        data["result_url"] = result_url(ev.recommendation_id)
    return f"id: {ev.event_id}\nevent: {ev.event_type}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def heartbeat() -> str:
    return f"event: heartbeat\ndata: {json.dumps({'server_time': datetime.now(UTC).isoformat()})}\n\n"


class ConnectionGuard:
    def __init__(self, max_per_user: int) -> None:
        self.max = max_per_user
        self._open: dict[str, int] = {}

    def acquire(self, user_id: str) -> bool:
        if self._open.get(user_id, 0) >= self.max:
            return False
        self._open[user_id] = self._open.get(user_id, 0) + 1
        return True

    def release(self, user_id: str) -> None:
        self._open[user_id] = max(0, self._open.get(user_id, 1) - 1)


class EventBridge:
    def __init__(self, settings: Settings, redis: Redis | None, agent: AgentClient) -> None:
        self.s = settings
        self.redis = redis
        self.agent = agent

    def _stream(self, request_id: UUID) -> str:
        return f"sta:{self.s.app_env}:run:{request_id}:events"

    async def stream(self, request_id: UUID, last_event_id: int | None) -> AsyncIterator[str]:
        deadline = time.monotonic() + self.s.sse_max_duration_seconds
        last_sent = last_event_id if last_event_id is not None else -1
        last_beat = time.monotonic()
        cursor = "0-0"
        while time.monotonic() < deadline:
            batch, cursor = await self._read(request_id, cursor)
            terminal = False
            for ev in batch:
                if ev.event_id <= last_sent:
                    continue  # reconnect with Last-Event-ID: never resend
                last_sent = ev.event_id
                yield format_event(ev)
                last_beat = time.monotonic()
                if ev.event_type in TERMINAL_EVENTS:
                    terminal = True
            if terminal:
                return
            if time.monotonic() - last_beat >= self.s.sse_heartbeat_seconds:
                yield heartbeat()
                last_beat = time.monotonic()
            if self.redis is None:
                await asyncio.sleep(self.s.sse_poll_interval_seconds)
        yield f"event: run.failed\ndata: {json.dumps({'error_code': 'DEPENDENCY_TIMEOUT', 'retryable': True})}\n\n"

    async def _read(self, request_id: UUID, cursor: str) -> tuple[list[RunProgressEvent], str]:
        events: list[RunProgressEvent] = []
        if self.redis is None:
            raw = await self.agent.events(request_id)
            return raw, cursor
        try:
            rows = await self.redis.xread(
                {self._stream(request_id): cursor}, block=int(self.s.sse_poll_interval_seconds * 1000), count=100
            )
        except Exception as exc:  # noqa: BLE001 - transient cache failure: heartbeat and retry
            log.warning("sse_stream_read_failed", error_type=type(exc).__name__)
            await asyncio.sleep(self.s.sse_poll_interval_seconds)
            return [], cursor
        for _name, entries in rows or []:
            for entry_id, fields in entries:
                cursor = entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
                raw = fields.get(b"data") or fields.get("data")
                ev = _parse(raw)
                if ev is not None:
                    events.append(ev)
        return events, cursor


def _parse(raw: Any) -> RunProgressEvent | None:
    try:
        return RunProgressEvent.model_validate_json(raw)
    except (ValidationError, TypeError, ValueError):
        log.warning("sse_event_rejected")  # off-contract payload never reaches the browser
        return None
