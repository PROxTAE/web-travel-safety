"""Schema ``recommendation`` (00_API_AND_DATA_CONTRACTS §8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "recommendation"


class Base(DeclarativeBase):
    pass


class RecommendationRow(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        UniqueConstraint("request_id", "decision_id", name="uq_recommendation_request_decision"),
        Index("ix_recommendation_trip", "trip_id", "created_at"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    decision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_pseudonym: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    risk: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FeedbackRow(Base):
    __tablename__ = "feedback"
    __table_args__ = (Index("ix_recommendation_feedback_rec", "recommendation_id"), {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_pseudonym: Mapped[str] = mapped_column(String(64), nullable=False)
    recommendation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    text_redacted: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, default="NONE")
    safety_review_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SafetyReviewRow(Base):
    __tablename__ = "safety_review_queue"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    feedback_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    assigned_to: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SubscriptionRow(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (Index("ix_recommendation_subs_trip", "trip_id", "status"), {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_pseudonym: Mapped[str] = mapped_column(String(64), nullable=False)
    trip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(8), nullable=False)
    consent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="ACTIVE")
    severity_threshold: Mapped[str] = mapped_column(String(8), nullable=False, default="MODERATE")
    locale: Mapped[str] = mapped_column(String(8), nullable=False, default="en-US")
    target_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )  # push sub / email ref only
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_recommendation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeliveryLogRow(Base):
    __tablename__ = "delivery_log"
    __table_args__ = (
        UniqueConstraint("subscription_id", "event_hash", "channel", name="uq_recommendation_delivery_event"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    subscription_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EmergencyContactRow(Base):
    __tablename__ = "emergency_contacts"
    __table_args__ = (UniqueConstraint("checksum", name="uq_recommendation_contact_checksum"), {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    subdivision: Mapped[str | None] = mapped_column(String(64))
    service_type: Mapped[str] = mapped_column(String(24), nullable=False)
    labels_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    authority: Mapped[str] = mapped_column(String(24), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    review_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    directory_version: Mapped[str] = mapped_column(String(24), nullable=False)
