"""SQLAlchemy tables for schema ``provider`` (00_API_AND_DATA_CONTRACTS §8).

Raw provider bodies are NOT stored (default policy); only hashes, status and quality metadata.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "provider"


class Base(DeclarativeBase):
    pass


class Provider(Base):
    __tablename__ = "providers"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    coverage_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    license_url: Mapped[str | None] = mapped_column(Text)
    attribution: Mapped[str | None] = mapped_column(Text)
    config_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0.0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FetchLog(Base):
    __tablename__ = "fetch_log"
    __table_args__ = (
        Index("ix_provider_fetch_log_provider_time", "provider_id", "fetched_at"),
        Index("ix_provider_fetch_log_query_hash", "query_hash"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_id: Mapped[str] = mapped_column(String(64), ForeignKey(f"{SCHEMA}.providers.id"), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64))
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(48))
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    from_cache: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    quality_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)


class ProviderHealthRow(Base):
    __tablename__ = "health"
    __table_args__ = {"schema": SCHEMA}

    provider_id: Mapped[str] = mapped_column(String(64), ForeignKey(f"{SCHEMA}.providers.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    quota_remaining: Mapped[int | None] = mapped_column(Integer)
    last_error_code: Mapped[str | None] = mapped_column(String(48))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
