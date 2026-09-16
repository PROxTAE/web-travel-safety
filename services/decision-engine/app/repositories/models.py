"""Schema ``decision``: policy/prompt registry + audit traces (no PII, no chain-of-thought)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sta_common.logging import get_logger

SCHEMA = "decision"
log = get_logger("decision-repo")


class Base(DeclarativeBase):
    pass


class PolicyVersion(Base):
    __tablename__ = "policy_versions"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditTrace(Base):
    __tablename__ = "audit_traces"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    input_hashes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    rules_fired: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    locked_action: Mapped[str] = mapped_column(String(16), nullable=False)
    llm_output_hash: Mapped[str | None] = mapped_column(String(64))
    validation_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    versions_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    trace_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditRepository:
    def __init__(self, factory: async_sessionmaker[AsyncSession] | None) -> None:
        self.factory = factory
        self.memory: list[dict[str, Any]] = []

    async def record(self, audit: dict[str, Any]) -> None:
        if self.factory is None:
            self.memory.append(audit)
            return
        try:
            async with self.factory() as s:
                s.add(
                    AuditTrace(
                        id=uuid.UUID(audit["decision_id"]),
                        request_id=uuid.UUID(audit["request_id"]),
                        input_hashes=audit["input_hashes"],
                        rules_fired=[audit["rule_fired"]],
                        locked_action=audit["locked_action"],
                        llm_output_hash=audit["llm"].get("output_hash"),
                        validation_json=audit["validation"],
                        versions_json=audit["versions"],
                        trace_json=audit,
                        created_at=datetime.fromisoformat(audit["created_at"]),
                    )
                )
                await s.commit()
        except Exception as exc:  # noqa: BLE001 - audit persistence failure is logged, decision already returned
            log.error("audit_persist_failed", error_type=type(exc).__name__)
