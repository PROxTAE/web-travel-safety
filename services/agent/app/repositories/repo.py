"""agent.runs + agent.tool_calls persistence (no secrets, no raw sensitive payloads) with in-memory fallback."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Float, Integer, String, select
from sqlalchemy.dialects.postgresql import JSONB, UUID, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sta_common.logging import get_logger
from sta_contracts.models import RunState

from app.tools.registry import ToolCallRecord

SCHEMA = "agent"
log = get_logger("agent-repo")
SENSITIVE_STATE_KEYS = {"external_context", "snapshot", "evidence_package", "decision"}  # large; keep refs only in DB


class Base(DeclarativeBase):
    pass


class RunRow(Base):
    __tablename__ = "runs"
    __table_args__ = {"schema": SCHEMA}
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    trip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    graph_version: Mapped[str] = mapped_column(String(48), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(16))
    policy_version: Mapped[str | None] = mapped_column(String(16))
    budgets_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    final_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    run_state_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ToolCallRow(Base):
    __tablename__ = "tool_calls"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(48))
    seq: Mapped[int] = mapped_column(Integer, nullable=False)


class RunRepository:
    def __init__(self, factory: async_sessionmaker[AsyncSession] | None) -> None:
        self.factory = factory
        self.memory_runs: dict[str, dict[str, Any]] = {}
        self.memory_states: dict[str, dict[str, Any]] = {}
        self.memory_tools: dict[str, list[ToolCallRecord]] = {}

    async def save_run(self, rs: RunState, state: dict[str, Any]) -> None:
        # the persisted state keeps references + the snapshot (needed for follow-up reuse), not provider payloads
        compact = {k: v for k, v in state.items() if k not in ("external_context", "evidence_package")}
        self.memory_states[str(rs.request_id)] = compact
        if self.factory is None:
            self.memory_runs[str(rs.request_id)] = json.loads(rs.model_dump_json())
            return
        try:
            async with self.factory() as s:
                stmt = insert(RunRow).values(
                    request_id=rs.request_id,
                    conversation_id=rs.conversation_id,
                    trip_id=rs.trip_id,
                    status=rs.status.value,
                    graph_version=rs.versions.graph or "",
                    prompt_version=rs.versions.prompt,
                    policy_version=rs.versions.policy,
                    budgets_json={
                        "steps": rs.step_count,
                        "tool_calls": rs.tool_call_count,
                        "llm_calls": rs.llm_call_count,
                    },
                    final_state=compact,
                    run_state_json=json.loads(rs.model_dump_json()),
                    created_at=rs.started_at,
                    updated_at=rs.updated_at,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=[RunRow.request_id],
                    set_={
                        "status": rs.status.value,
                        "prompt_version": rs.versions.prompt,
                        "policy_version": rs.versions.policy,
                        "budgets_json": stmt.excluded.budgets_json,
                        "final_state": compact,
                        "run_state_json": stmt.excluded.run_state_json,
                        "updated_at": rs.updated_at,
                    },
                )
                await s.execute(stmt)
                await s.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("run_persist_failed", error_type=type(exc).__name__)

    async def latest_state_for_conversation(self, conversation_id: str, *, exclude: str) -> dict[str, Any] | None:
        if self.factory is None:
            cands = [
                st
                for rid, st in self.memory_states.items()
                if st.get("conversation_id") == conversation_id and rid != exclude and st.get("snapshot")
            ]
            return max(cands, key=lambda st: st.get("started_at", "")) if cands else None
        async with self.factory() as s:
            row = (
                await s.execute(
                    select(RunRow)
                    .where(
                        RunRow.conversation_id == uuid.UUID(conversation_id), RunRow.request_id != uuid.UUID(exclude)
                    )
                    .order_by(RunRow.updated_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            return dict(row.final_state) if row else None

    async def save_tool_calls(self, request_id: str, records: list[ToolCallRecord]) -> None:
        self.memory_tools[request_id] = list(records)
        if self.factory is None or not records:
            return
        try:
            async with self.factory() as s:
                for i, r in enumerate(records):
                    s.add(
                        ToolCallRow(
                            request_id=uuid.UUID(request_id),
                            tool_name=r.tool,
                            input_hash=r.input_hash,
                            status=r.status,
                            started_at=datetime.now(UTC),
                            duration_ms=r.duration_ms,
                            error_code=r.error_code,
                            seq=i,
                        )
                    )
                await s.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("tool_calls_persist_failed", error_type=type(exc).__name__)
