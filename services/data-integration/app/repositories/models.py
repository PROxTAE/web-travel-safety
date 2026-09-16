"""Schema ``integration`` (00_API_AND_DATA_CONTRACTS §8)."""

from __future__ import annotations

import uuid
from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "integration"


class Base(DeclarativeBase):
    pass


class Snapshot(Base):
    __tablename__ = "snapshots"
    __table_args__ = (
        UniqueConstraint("request_id", "input_content_hash", "schema_version", name="uq_integration_snapshot_input"),
        Index("ix_integration_snapshots_request", "request_id"),
        Index("ix_integration_snapshots_trip", "trip_id", "created_at"),
        Index("ix_integration_snapshots_corridor", "corridor", postgresql_using="gist"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trip_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    input_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    feature_schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    transform_version: Mapped[str] = mapped_column(String(16), nullable=False)
    gate: Mapped[str] = mapped_column(String(16), nullable=False)
    quality_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    features_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    snapshot_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)  # immutable full payload
    corridor = mapped_column(Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False), nullable=True)
    departure_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    arrival_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DisasterEventRow(Base):
    __tablename__ = "disaster_events"
    __table_args__ = (
        UniqueConstraint("event_id", "content_hash", name="uq_integration_event_hash"),
        Index("ix_integration_events_geom", "geom", postgresql_using="gist"),
        Index("ix_integration_events_time", "effective_at", "ends_at"),
        Index("ix_integration_events_severity", "severity", "official"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    official: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    closure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    geom = mapped_column(Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False), nullable=False)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    record_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class WeatherRecordRow(Base):
    __tablename__ = "weather_records"
    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_integration_weather_hash"),
        Index("ix_integration_weather_geom", "geom", postgresql_using="gist"),
        Index("ix_integration_weather_valid", "valid_at", postgresql_using="brin"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    valid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    geom = mapped_column(Geometry(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False)
    record_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class TransportRecordRow(Base):
    __tablename__ = "transport_records"
    __table_args__ = (
        Index("ix_integration_transport_service", "operator", "service_number", "status"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    operator: Mapped[str | None] = mapped_column(String(128))
    service_number: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    record_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class LineageRowModel(Base):
    __tablename__ = "lineage"
    __table_args__ = (
        Index("ix_integration_lineage_record", "snapshot_id", "record_type", "record_id"),
        {"schema": SCHEMA},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    record_type: Mapped[str] = mapped_column(String(16), nullable=False)
    record_id: Mapped[str] = mapped_column(String(160), nullable=False)
    field_path: Mapped[str] = mapped_column(String(128), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    transform: Mapped[str] = mapped_column(String(64), nullable=False)
    transform_version: Mapped[str] = mapped_column(String(16), nullable=False)


class QuarantineRow(Base):
    __tablename__ = "quarantine"
    __table_args__ = (Index("ix_integration_quarantine_status", "status", "created_at"), {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    record_type: Mapped[str] = mapped_column(String(16), nullable=False)
    record_id: Mapped[str] = mapped_column(String(160), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    error_code: Mapped[str] = mapped_column(String(64), nullable=False)
    field_path: Mapped[str] = mapped_column(String(128), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


__all__ = [
    "Base",
    "SCHEMA",
    "Snapshot",
    "DisasterEventRow",
    "WeatherRecordRow",
    "TransportRecordRow",
    "LineageRowModel",
    "QuarantineRow",
    "JSON",
    "Float",
]
