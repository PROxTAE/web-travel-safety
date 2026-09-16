"""Repositories with PostgreSQL implementation and an in-memory fallback (tests / APP_ENV=test)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sta_contracts.models import AlertSubscription, FeedbackEvent, RecommendationResponse

from app.repositories.models import DeliveryLogRow, FeedbackRow, RecommendationRow, SafetyReviewRow, SubscriptionRow


class Repository:
    def __init__(self, factory: async_sessionmaker[AsyncSession] | None) -> None:
        self.factory = factory
        self._recs: dict[UUID, RecommendationResponse] = {}
        self._rec_owner: dict[UUID, str | None] = {}
        self._feedback: dict[UUID, FeedbackEvent] = {}
        self._feedback_owner: dict[UUID, str] = {}
        self._reviews: list[dict[str, Any]] = []
        self._subs: dict[UUID, dict[str, Any]] = {}
        self._deliveries: dict[tuple[UUID, str, str], dict[str, Any]] = {}

    # ---------------------------------------------------------------- recommendations
    async def save_recommendation(
        self, rec: RecommendationResponse, user_pseudonym: str | None
    ) -> RecommendationResponse:
        if self.factory is None:
            for r in self._recs.values():
                if r.request_id == rec.request_id and r.decision_id == rec.decision_id:
                    return r
            self._recs[rec.recommendation_id] = rec
            self._rec_owner[rec.recommendation_id] = user_pseudonym
            return rec
        async with self.factory() as s:
            existing = (
                await s.execute(
                    select(RecommendationRow).where(
                        RecommendationRow.request_id == rec.request_id, RecommendationRow.decision_id == rec.decision_id
                    )
                )
            ).scalar_one_or_none()
            if existing:
                return RecommendationResponse.model_validate(existing.response_json)
            s.add(
                RecommendationRow(
                    id=rec.recommendation_id,
                    request_id=rec.request_id,
                    trip_id=rec.trip_id,
                    decision_id=rec.decision_id,
                    user_pseudonym=user_pseudonym,
                    action=rec.action_code.value,
                    risk=rec.risk_level.value,
                    status=rec.status.value,
                    response_json=json.loads(rec.model_dump_json()),
                    supersedes_id=rec.supersedes_recommendation_id,
                    expires_at=rec.expires_at,
                    created_at=rec.created_at,
                )
            )
            try:
                await s.commit()
            except IntegrityError:
                await s.rollback()
                existing = (
                    await s.execute(
                        select(RecommendationRow).where(
                            RecommendationRow.request_id == rec.request_id,
                            RecommendationRow.decision_id == rec.decision_id,
                        )
                    )
                ).scalar_one()
                return RecommendationResponse.model_validate(existing.response_json)
            return rec

    async def get_recommendation(self, rec_id: UUID) -> tuple[RecommendationResponse | None, str | None]:
        if self.factory is None:
            return self._recs.get(rec_id), self._rec_owner.get(rec_id)
        async with self.factory() as s:
            row = await s.get(RecommendationRow, rec_id)
            return (
                (RecommendationResponse.model_validate(row.response_json), row.user_pseudonym) if row else (None, None)
            )

    async def latest_for_trip(self, trip_id: UUID) -> RecommendationResponse | None:
        if self.factory is None:
            cands = [r for r in self._recs.values() if r.trip_id == trip_id]
            return max(cands, key=lambda r: r.created_at) if cands else None
        async with self.factory() as s:
            row = (
                await s.execute(
                    select(RecommendationRow)
                    .where(RecommendationRow.trip_id == trip_id)
                    .order_by(RecommendationRow.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            return RecommendationResponse.model_validate(row.response_json) if row else None

    # ---------------------------------------------------------------- feedback
    async def save_feedback(self, fb: FeedbackEvent, user_pseudonym: str, review_severity: str | None) -> FeedbackEvent:
        review_id = uuid4() if review_severity else None
        fb = fb.model_copy(
            update={"review_status": "QUEUED" if review_severity else "NONE", "safety_review_id": review_id}
        )
        if self.factory is None:
            self._feedback[fb.id] = fb
            self._feedback_owner[fb.id] = user_pseudonym
            if review_severity:
                self._reviews.append(
                    {"id": review_id, "feedback_id": fb.id, "severity": review_severity, "status": "OPEN"}
                )
            return fb
        async with self.factory() as s:
            s.add(
                FeedbackRow(
                    id=fb.id,
                    user_pseudonym=user_pseudonym,
                    recommendation_id=fb.recommendation_id,
                    category=fb.category.value,
                    text_redacted=fb.text_redacted,
                    review_status=fb.review_status,
                    safety_review_id=review_id,
                    created_at=fb.created_at,
                )
            )
            if review_severity:
                s.add(
                    SafetyReviewRow(
                        id=review_id,
                        feedback_id=fb.id,
                        severity=review_severity,
                        status="OPEN",
                        created_at=fb.created_at,
                    )
                )
            await s.commit()
            return fb

    async def review_queue(self, status: str = "OPEN") -> list[dict[str, Any]]:
        if self.factory is None:
            return [r for r in self._reviews if r["status"] == status]
        async with self.factory() as s:
            rows = (await s.execute(select(SafetyReviewRow).where(SafetyReviewRow.status == status))).scalars().all()
            return [
                {"id": r.id, "feedback_id": r.feedback_id, "severity": r.severity, "status": r.status} for r in rows
            ]

    async def export_feedback(self) -> list[dict[str, Any]]:
        """Pseudonymized export for OFFLINE evaluation only (never used to update a model at runtime)."""
        if self.factory is None:
            return [
                {
                    "feedback_id": str(f.id),
                    "recommendation_id": str(f.recommendation_id),
                    "category": f.category.value,
                    "review_status": f.review_status,
                    "created_at": f.created_at.isoformat(),
                }
                for f in self._feedback.values()
            ]
        async with self.factory() as s:
            rows = (await s.execute(select(FeedbackRow).where(FeedbackRow.deleted_at.is_(None)))).scalars().all()
            return [
                {
                    "feedback_id": str(r.id),
                    "recommendation_id": str(r.recommendation_id),
                    "category": r.category,
                    "review_status": r.review_status,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]

    # ---------------------------------------------------------------- subscriptions
    async def save_subscription(
        self, sub: AlertSubscription, user_pseudonym: str, target: dict[str, Any]
    ) -> AlertSubscription:
        if self.factory is None:
            self._subs[sub.id] = {"sub": sub, "user": user_pseudonym, "target": target, "last": None}
            return sub
        async with self.factory() as s:
            s.add(
                SubscriptionRow(
                    id=sub.id,
                    user_pseudonym=user_pseudonym,
                    trip_id=sub.trip_id,
                    channel=sub.channel.value,
                    consent_id=sub.consent_id,
                    status=sub.status,
                    severity_threshold=sub.severity_threshold.value,
                    locale=sub.locale,
                    target_json=target,
                    cooldown_until=None,
                    created_at=sub.created_at,
                )
            )
            await s.commit()
            return sub

    async def get_subscription(self, sub_id: UUID) -> tuple[AlertSubscription | None, str | None, dict[str, Any]]:
        if self.factory is None:
            x = self._subs.get(sub_id)
            return (x["sub"], x["user"], x["target"]) if x else (None, None, {})
        async with self.factory() as s:
            row = await s.get(SubscriptionRow, sub_id)
            return (self._sub_from_row(row), row.user_pseudonym, row.target_json) if row else (None, None, {})

    async def revoke_subscription(self, sub_id: UUID) -> None:
        now = datetime.now(UTC)
        if self.factory is None:
            x = self._subs.get(sub_id)
            if x:
                x["sub"] = x["sub"].model_copy(update={"status": "REVOKED", "revoked_at": now})
            return
        async with self.factory() as s:
            await s.execute(
                update(SubscriptionRow).where(SubscriptionRow.id == sub_id).values(status="REVOKED", revoked_at=now)
            )
            await s.commit()

    async def revoke_by_consent(self, consent_id: UUID) -> int:
        now = datetime.now(UTC)
        if self.factory is None:
            n = 0
            for x in self._subs.values():
                if x["sub"].consent_id == consent_id and x["sub"].status == "ACTIVE":
                    x["sub"] = x["sub"].model_copy(update={"status": "REVOKED", "revoked_at": now})
                    n += 1
            return n
        async with self.factory() as s:
            res = await s.execute(
                update(SubscriptionRow)
                .where(SubscriptionRow.consent_id == consent_id, SubscriptionRow.status == "ACTIVE")
                .values(status="REVOKED", revoked_at=now)
            )
            await s.commit()
            return int(getattr(res, "rowcount", 0) or 0)

    async def active_subscriptions_for_trip(
        self, trip_id: UUID
    ) -> list[tuple[AlertSubscription, str, dict[str, Any], UUID | None]]:
        if self.factory is None:
            return [
                (x["sub"], x["user"], x["target"], x["last"])
                for x in self._subs.values()
                if x["sub"].trip_id == trip_id and x["sub"].status == "ACTIVE"
            ]
        async with self.factory() as s:
            rows = (
                (
                    await s.execute(
                        select(SubscriptionRow).where(
                            SubscriptionRow.trip_id == trip_id, SubscriptionRow.status == "ACTIVE"
                        )
                    )
                )
                .scalars()
                .all()
            )
            return [(self._sub_from_row(r), r.user_pseudonym, r.target_json, r.last_recommendation_id) for r in rows]

    async def all_active_subscriptions(self) -> list[AlertSubscription]:
        if self.factory is None:
            return [x["sub"] for x in self._subs.values() if x["sub"].status == "ACTIVE"]
        async with self.factory() as s:
            rows = (await s.execute(select(SubscriptionRow).where(SubscriptionRow.status == "ACTIVE"))).scalars().all()
            return [self._sub_from_row(r) for r in rows]

    async def mark_subscription(
        self, sub_id: UUID, *, cooldown_until: datetime | None, last_recommendation_id: UUID
    ) -> None:
        if self.factory is None:
            x = self._subs.get(sub_id)
            if x:
                x["sub"] = x["sub"].model_copy(update={"cooldown_until": cooldown_until})
                x["last"] = last_recommendation_id
            return
        async with self.factory() as s:
            await s.execute(
                update(SubscriptionRow)
                .where(SubscriptionRow.id == sub_id)
                .values(cooldown_until=cooldown_until, last_recommendation_id=last_recommendation_id)
            )
            await s.commit()

    @staticmethod
    def _sub_from_row(r: SubscriptionRow) -> AlertSubscription:
        return AlertSubscription(
            id=r.id,
            trip_id=r.trip_id,
            channel=r.channel,
            consent_id=r.consent_id,
            status=r.status,
            severity_threshold=r.severity_threshold,
            locale=r.locale,
            cooldown_until=r.cooldown_until,
            created_at=r.created_at,
            revoked_at=r.revoked_at,
        )

    # ---------------------------------------------------------------- deliveries (idempotent)
    async def claim_delivery(self, sub_id: UUID, event_hash: str, channel: str) -> bool:
        """True if this (subscription, event, channel) was not delivered before (unique constraint)."""
        if self.factory is None:
            key = (sub_id, event_hash, channel)
            if key in self._deliveries:
                return False
            self._deliveries[key] = {"status": "PENDING"}
            return True
        async with self.factory() as s:
            stmt = (
                insert(DeliveryLogRow)
                .values(
                    id=uuid4(),
                    subscription_id=sub_id,
                    event_hash=event_hash,
                    channel=channel,
                    status="PENDING",
                    attempted_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing(constraint="uq_recommendation_delivery_event")
            )
            res = await s.execute(stmt)
            await s.commit()
            return bool(getattr(res, "rowcount", 0))

    async def finish_delivery(
        self,
        sub_id: UUID,
        event_hash: str,
        channel: str,
        status: str,
        provider_message_id: str | None,
        error_code: str | None,
    ) -> None:
        if self.factory is None:
            self._deliveries[(sub_id, event_hash, channel)] = {
                "status": status,
                "provider_message_id": provider_message_id,
                "error_code": error_code,
            }
            return
        async with self.factory() as s:
            await s.execute(
                update(DeliveryLogRow)
                .where(
                    DeliveryLogRow.subscription_id == sub_id,
                    DeliveryLogRow.event_hash == event_hash,
                    DeliveryLogRow.channel == channel,
                )
                .values(status=status, provider_message_id=provider_message_id, error_code=error_code)
            )
            await s.commit()

    def deliveries(self) -> dict[tuple[UUID, str, str], dict[str, Any]]:
        return self._deliveries
