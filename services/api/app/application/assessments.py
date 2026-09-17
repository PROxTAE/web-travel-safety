"""Assessment orchestration: idempotent request creation, agent submission, status sync, cancellation.

The API never computes risk and never edits a decision: it normalizes the trip into a ``TravelRequest``, commits the
request row first, submits to the agent, then mirrors the agent's run state onto ``travel.requests`` through the
state machine in ``domain.rules``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import orjson
from redis.asyncio import Redis
from sta_common.cache import key as redis_key
from sta_common.errors import AppError, ErrorCode
from sta_common.logging import get_logger
from sta_contracts.enums import Intent, RunStatus, TravelMode
from sta_contracts.models import LocationRef, RunRef, RunState, TravelPreference, TravelRequest, Trip

from app.auth.deps import CurrentUser, user_scope_hash
from app.clients.downstream import AgentClient
from app.domain import rules
from app.repositories.repo import Repository
from app.settings import Settings

log = get_logger("assessments")


def poll_url(request_id: UUID) -> str:
    return f"/api/v1/runs/{request_id}"


def events_url(request_id: UUID) -> str:
    return f"/api/v1/runs/{request_id}/events"


def result_url(rec_id: UUID | None) -> str | None:
    return f"/api/v1/recommendations/{rec_id}" if rec_id else None


@dataclass(slots=True)
class StartResult:
    ref: RunRef
    replayed: bool = False  # True when an Idempotency-Key replay returned the existing run


@dataclass(slots=True)
class StartParams:
    trip: Trip
    idempotency_key: str | None
    question: str | None = None
    kind: str = "ASSESSMENT"
    conversation_id: UUID | None = None
    resume_from: UUID | None = None
    intent_hint: Intent | None = None
    previous_recommendation_id: UUID | None = None


class AssessmentService:
    def __init__(self, settings: Settings, repo: Repository, agent: AgentClient, redis: Redis | None) -> None:
        self.s = settings
        self.repo = repo
        self.agent = agent
        self.redis = redis
        self._mem_idem: dict[str, dict[str, str]] = {}

    # ------------------------------------------------------------------ idempotency
    async def _idem_get(self, user_id: UUID, key_hash: str) -> dict[str, str] | None:
        k = redis_key(self.s.app_env, "idempotency", str(user_id), key_hash)
        if self.redis is not None:
            raw = await self.redis.get(k)
            if raw:
                out: dict[str, str] = orjson.loads(raw)
                return out
        elif k in self._mem_idem:
            return self._mem_idem[k]
        row = await self.repo.requests.by_idempotency(user_id, key_hash)
        return {"fingerprint": row["fingerprint"], "request_id": str(row["id"])} if row else None

    async def _idem_put(self, user_id: UUID, key_hash: str, fingerprint: str, request_id: UUID) -> None:
        k = redis_key(self.s.app_env, "idempotency", str(user_id), key_hash)
        value = {"fingerprint": fingerprint, "request_id": str(request_id)}
        if self.redis is not None:
            await self.redis.set(k, orjson.dumps(value), ex=self.s.idempotency_ttl_seconds, nx=True)
        else:
            self._mem_idem[k] = value

    # ------------------------------------------------------------------ start
    async def start(self, user: CurrentUser, p: StartParams) -> StartResult:
        trip = p.trip
        if trip.status == "DELETED":
            raise AppError(ErrorCode.CONFLICT, "trip is deleted")
        rules.validate_location(trip.origin, "origin", require_confirmed=True)
        rules.validate_location(trip.destination, "destination", require_confirmed=True)
        rules.validate_trip_times(
            trip.departure_time,
            trip.return_time,
            max_horizon_days=self.s.max_trip_horizon_days,
            max_past_minutes=self.s.max_past_departure_minutes,
        )
        payload = {
            "trip_id": str(trip.id),
            "trip_revision": trip.revision,
            "question": p.question,
            "kind": p.kind,
            "conversation_id": str(p.conversation_id) if p.conversation_id else None,
            "resume_from": str(p.resume_from) if p.resume_from else None,
        }
        key_hash: str | None = None
        fingerprint: str | None = None
        if p.idempotency_key:
            key_hash = hashlib.sha256(f"{user.id}:{p.idempotency_key}".encode()).hexdigest()
            fingerprint = rules.idempotency_fingerprint(str(user.id), p.idempotency_key, payload)
            existing = await self._idem_get(user.id, key_hash)
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise AppError(ErrorCode.IDEMPOTENCY_CONFLICT, "Idempotency-Key was used with a different payload")
                row = await self.repo.requests.get(user.id, UUID(existing["request_id"]))
                if row is not None:
                    return StartResult(self._ref_from_row(row), replayed=True)

        row = await self.repo.requests.create(
            {
                "trip_id": trip.id,
                "user_id": user.id,
                "conversation_id": p.conversation_id,
                "kind": p.kind,
                "idempotency_key_hash": key_hash,
                "fingerprint": fingerprint,
                "contract_version": self.s.contract_version,
                "trip_revision": trip.revision,
                "recommendation_id": None,
                "previous_recommendation_id": p.previous_recommendation_id,
                "error_code": None,
            }
        )
        request_id: UUID = row["id"]
        if key_hash and fingerprint:
            await self._idem_put(user.id, key_hash, fingerprint, request_id)

        travel_request = TravelRequest(
            request_id=request_id,
            trip_id=trip.id,
            conversation_id=p.conversation_id,
            user_scope_hash=user_scope_hash(user.id, self.s.user_scope_salt.get_secret_value()),
            origin=LocationRef.model_validate(trip.origin.model_dump()),
            destination=LocationRef.model_validate(trip.destination.model_dump()),
            departure_time=trip.departure_time,
            return_time=trip.return_time,
            travel_modes=[TravelMode(m) for m in trip.travel_modes],
            preferences=TravelPreference.model_validate(trip.preferences.model_dump()),
            question=p.question,
            intent_hint=p.intent_hint,
            locale=user.locale,
            timezone=trip.timezone or user.timezone,
            supersedes_request_id=p.resume_from,
            trip_revision=trip.revision,
        )
        try:
            ref = await self.agent.start(travel_request, p.conversation_id, p.resume_from)
        except AppError as exc:
            await self.repo.requests.set_status(request_id, "FAILED", error_code=exc.code.value)
            log.warning("agent_submit_failed", error_code=exc.code.value)
            raise
        rules.assert_transition("CREATED", ref.status.value)
        await self.repo.requests.set_status(request_id, ref.status.value)
        await self.repo.trips.set_fields(user.id, trip.id, {"latest_request_id": request_id})
        await self.repo.audit.log(user.id, "assessment.start", "request", str(request_id), kind=p.kind)
        return StartResult(
            RunRef(
                request_id=request_id,
                status=ref.status,
                events_url=events_url(request_id),
                poll_url=poll_url(request_id),
                submitted_at=ref.submitted_at,
                conversation_id=p.conversation_id,
            )
        )

    def _ref_from_row(self, row: dict[str, Any]) -> RunRef:
        return RunRef(
            request_id=row["id"],
            status=RunStatus(row["status"]) if row["status"] in RunStatus.__members__ else RunStatus.QUEUED,
            events_url=events_url(row["id"]),
            poll_url=poll_url(row["id"]),
            submitted_at=row["created_at"],
            recommendation_id=row.get("recommendation_id"),
            conversation_id=row.get("conversation_id"),
        )

    # ------------------------------------------------------------------ state
    async def state(self, user: CurrentUser, request_id: UUID) -> dict[str, Any]:
        row = await self.repo.requests.get(user.id, request_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "run not found")
        agent_state: RunState | None = None
        if row["status"] not in rules.TERMINAL:
            try:
                agent_state = await self.agent.state(request_id)
            except AppError as exc:
                if not exc.retryable or exc.details.get("reason") == "contract_violation":
                    raise
                log.warning("agent_state_unavailable", error_code=exc.code.value)
        if agent_state is not None:
            row = await self._sync(user, row, agent_state)
        return self._public_view(row, agent_state)

    async def _sync(self, user: CurrentUser, row: dict[str, Any], st: RunState) -> dict[str, Any]:
        new = st.status.value
        if new != row["status"]:
            try:
                rules.assert_transition(row["status"], new)
            except AppError:
                log.warning("run_state_regression_ignored", from_status=row["status"], to_status=new)
                return row
            updated = await self.repo.requests.set_status(
                row["id"], new, recommendation_id=st.recommendation_id, error_code=st.error_code
            )
            row = updated or row
            if st.recommendation_id:
                await self.repo.trips.set_fields(
                    user.id, row["trip_id"], {"latest_recommendation_id": st.recommendation_id}
                )
                if row.get("conversation_id"):
                    await self.repo.conversations.add_message(
                        user.id,
                        row["conversation_id"],
                        "assistant",
                        None,
                        request_id=row["id"],
                        recommendation_id=st.recommendation_id,
                    )
        return row

    @staticmethod
    def _public_view(row: dict[str, Any], st: RunState | None) -> dict[str, Any]:
        rec_id = row.get("recommendation_id") or (st.recommendation_id if st else None)
        out: dict[str, Any] = {
            "request_id": str(row["id"]),
            "trip_id": str(row["trip_id"]),
            "conversation_id": str(row["conversation_id"]) if row.get("conversation_id") else None,
            "kind": row["kind"],
            "status": row["status"],
            "stage": st.stage.value if st and st.stage else None,
            "missing_fields": list(st.missing_fields) if st else [],
            "degraded_services": list(st.degraded_services) if st else [],
            "error_code": row.get("error_code") or (st.error_code if st else None),
            "recommendation_id": str(rec_id) if rec_id else None,
            "result_url": result_url(rec_id),
            "events_url": events_url(row["id"]),
            "poll_url": poll_url(row["id"]),
            "versions": st.versions.model_dump(mode="json") if st else None,
            "submitted_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row.get("completed_at"),
        }
        return out

    # ------------------------------------------------------------------ cancel
    async def cancel(self, user: CurrentUser, request_id: UUID, reason: str) -> dict[str, Any]:
        row = await self.repo.requests.get(user.id, request_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "run not found")
        if row["status"] in rules.TERMINAL:
            return self._public_view(row, None)
        await self.agent.cancel(request_id, reason)
        rules.assert_transition(row["status"], "CANCELLED")
        updated = await self.repo.requests.set_status(request_id, "CANCELLED")
        await self.repo.audit.log(user.id, "assessment.cancel", "request", str(request_id))
        return self._public_view(updated or row, None)

    async def mark_failed_if_stale(self, row: dict[str, Any]) -> None:  # used by the alerts consumer watcher
        if row["status"] not in rules.TERMINAL:
            await self.repo.requests.set_status(row["id"], "FAILED", error_code="DEPENDENCY_TIMEOUT")


def utcnow() -> datetime:
    return datetime.now(UTC)
