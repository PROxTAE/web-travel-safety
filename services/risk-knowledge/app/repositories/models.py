"""Schema ``knowledge`` (00_API_AND_DATA_CONTRACTS §8): model registry, documents, chunks, assessments, route evals."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "knowledge"


class Base(DeclarativeBase):
    pass


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)  # CANDIDATE|APPROVED|ACTIVE|RETIRED
    feature_schema: Mapped[str] = mapped_column(String(16), nullable=False)
    metrics_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    artifact_uri: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_manifest_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    approved_by: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    authority: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    country: Mapped[str] = mapped_column(String(8), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    review_status: Mapped[str] = mapped_column(String(16), nullable=False)
    collection_version: Mapped[str] = mapped_column(String(32), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChunkRow(Base):
    __tablename__ = "chunks"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(64), nullable=False)
    section: Mapped[str | None] = mapped_column(String(160))
    page: Mapped[int | None] = mapped_column(Integer)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    qdrant_point_id: Mapped[str] = mapped_column(String(64), nullable=False)
    collection_version: Mapped[str] = mapped_column(String(32), nullable=False)


class RiskAssessmentRow(Base):
    __tablename__ = "risk_assessments"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    route_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(8), nullable=False)
    model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    thresholds_version: Mapped[str] = mapped_column(String(16), nullable=False)
    payload_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RouteEvaluationRow(Base):
    __tablename__ = "route_evaluations"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    ranking_version: Mapped[str] = mapped_column(String(16), nullable=False)
    recommended_route_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
