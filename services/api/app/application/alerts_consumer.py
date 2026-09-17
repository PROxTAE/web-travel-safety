"""Consumes ``alert.reassessment.requested`` (published by the recommendation worker) and turns each trip into a
system-initiated reassessment; on completion it asks the recommendation service to evaluate alert deliveries."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sta_common.errors import AppError
from sta_common.logging import get_logger

from app.application.assessments import AssessmentService, StartParams
from app.auth.deps import CurrentUser
from app.auth.jwt import Principal
from app.clients.downstream import RecommendationClient
from app.domain import rules
from app.repositories import models as m
from app.repositories.repo import Repository, to_trip
from app.settings import Settings

log = get_logger("alerts-consumer")


def system_user(row: dict[str, Any]) -> CurrentUser:
    principal = Principal(subject="system", roles=frozenset(), scopes=frozenset(), issuer="system", expires_at=0)
    return CurrentUser(id=row["id"], principal=principal, locale=row["locale"], timezone=row["timezone"], deleted=False)


class ReassessmentConsumer:
    def __init__(
        self,
        settings: Settings,
        redis: Redis | None,
        repo: Repository,
        assessments: AssessmentService,
        recommendation: RecommendationClient,
    ) -> None:
        self.s = settings
        self.redis = redis
        self.repo = repo
        self.assessments = assessments
        self.rec = recommendation
        self.stream = f"sta:{settings.app_env}:stream:{settings.alerts_stream}"
        self.consumer = f"{settings.service_name}-{id(self) & 0xFFFF:x}"
        self._task: asyncio.Task[None] | None = None
        self.processed = 0

    async def start(self) -> None:
        if self.redis is None or not self.s.alerts_consumer_enabled:
            return
        try:
            await self.redis.xgroup_create(self.stream, self.s.alerts_consumer_group, id="$", mkstream=True)
        except ResponseError as exc:  # BUSYGROUP: group already exists
            if "BUSYGROUP" not in str(exc):
                raise
        self._task = asyncio.create_task(self._loop(), name="alerts-consumer")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                log.info("alerts_consumer_stopped", error_type=type(exc).__name__)

    async def _loop(self) -> None:
        assert self.redis is not None
        while True:
            try:
                rows = await self.redis.xreadgroup(
                    self.s.alerts_consumer_group, self.consumer, {self.stream: ">"}, count=20, block=2000
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("alerts_stream_read_failed", error_type=type(exc).__name__)
                await asyncio.sleep(5)
                continue
            for _name, entries in rows or []:
                for entry_id, fields in entries:
                    try:
                        await self.handle(fields)
                    except Exception as exc:  # noqa: BLE001 - one bad event must not stop the consumer
                        log.warning("alerts_event_failed", error_type=type(exc).__name__)
                    await self.redis.xack(self.stream, self.s.alerts_consumer_group, entry_id)

    async def handle(self, fields: dict[Any, Any]) -> UUID | None:
        raw = fields.get(b"payload") or fields.get("payload")
        payload = json.loads(raw) if raw else {}
        trip_id = payload.get("trip_id")
        if not trip_id:
            return None
        event_id = fields.get(b"event_id") or fields.get("event_id") or ""
        event_id = event_id.decode() if isinstance(event_id, bytes) else str(event_id)
        trip_row = await self.repo.store.get(m.trips, UUID(trip_id))
        if trip_row is None or trip_row.get("deleted_at"):
            return None
        user_row = await self.repo.users.get(trip_row["user_id"])
        if user_row is None or user_row.get("deleted_at"):
            return None
        trip = to_trip(trip_row)
        previous = trip.latest_recommendation_id
        user = system_user(user_row)
        try:
            result = await self.assessments.start(
                user,
                StartParams(
                    trip=trip,
                    idempotency_key=f"reassess:{event_id}" if event_id else None,
                    kind="REASSESSMENT",
                    previous_recommendation_id=previous,
                ),
            )
        except AppError as exc:
            log.warning("reassessment_rejected", error_code=exc.code.value)
            return None
        ref = result.ref
        self.processed += 1
        asyncio.create_task(self._watch(user, ref.request_id, previous), name=f"reassess-watch-{ref.request_id}")
        return ref.request_id

    async def _watch(self, user: CurrentUser, request_id: UUID, previous: UUID | None) -> None:
        for _ in range(int(self.s.agent_submit_timeout_seconds * 12)):
            await asyncio.sleep(1.0)
            try:
                st = await self.assessments.state(user, request_id)
            except AppError:
                continue
            if st["status"] in rules.TERMINAL:
                if st["recommendation_id"]:
                    try:
                        decisions = await self.rec.alerts_evaluate(UUID(st["recommendation_id"]), previous)
                        log.info("alerts_evaluated", request_id=str(request_id), deliveries=len(decisions))
                    except AppError as exc:
                        log.warning("alerts_evaluate_failed", error_code=exc.code.value)
                return
        row = await self.repo.requests.get(user.id, request_id)
        if row:
            await self.assessments.mark_failed_if_stale(row)
