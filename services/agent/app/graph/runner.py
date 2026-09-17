"""Run lifecycle: create state, execute the graph under budgets/deadline, persist run state and tool audit."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sta_common.context import RequestContext, set_context
from sta_common.errors import AppError, ErrorCode
from sta_common.http import ResilientClient
from sta_common.logging import get_logger
from sta_contracts.enums import Intent, RunStage, RunStatus
from sta_contracts.models import AgentRunRequest, RunRef, RunState, VersionInfo

from app.graph.builder import GRAPH_VERSION, build_graph, graph_checksum
from app.graph.nodes.core import NodeContext
from app.graph.state import AgentState
from app.progress.publisher import ProgressPublisher
from app.repositories.repo import RunRepository
from app.settings import Settings
from app.tools.registry import Budget, ToolRunner

log = get_logger("runner")


class RunManager:
    def __init__(
        self,
        settings: Settings,
        progress: ProgressPublisher,
        repo: RunRepository,
        clients: dict[str, ResilientClient],
        checkpointer: Any = None,
    ) -> None:
        self.s = settings
        self.progress = progress
        self.repo = repo
        self.clients = clients
        self.checkpointer = checkpointer
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.states: dict[str, AgentState] = {}

    # ------------------------------------------------------------------ public
    async def start(
        self, req: AgentRunRequest, *, correlation_id: str, resume_state: AgentState | None = None
    ) -> RunRef:
        tr = req.travel_request
        rid = str(tr.request_id)
        now = datetime.now(UTC)
        state: AgentState = resume_state or {
            "request_id": rid,
            "correlation_id": correlation_id,
            "trip_id": str(tr.trip_id),
            "conversation_id": str(req.conversation_id or tr.conversation_id)
            if (req.conversation_id or tr.conversation_id)
            else None,
            "user_scope_hash": tr.user_scope_hash,
            "travel_request": tr.model_dump(mode="json"),
            "intent": None,
            "missing_fields": [],
            "question": tr.question,
            "graph_version": GRAPH_VERSION,
            "current_stage": None,
            "status": RunStatus.QUEUED.value,
            "step_count": 0,
            "tool_call_count": 0,
            "llm_call_count": 0,
            "started_at": now.isoformat(),
            "deadline_at": (now + timedelta(seconds=self.s.agent_total_timeout_seconds)).isoformat(),
            "cancelled": False,
            "errors": [],
            "external_context": None,
            "snapshot": None,
            "snapshot_id": None,
            "snapshot_created_at": None,
            "evidence_package": None,
            "decision": None,
            "recommendation_id": None,
            "decision_id": None,
            "degraded_services": [],
            "quality_gate": None,
            "limitations": [],
            "previous_recommendation_id": None,
            "reuse_snapshot": False,
            "versions": {"graph": GRAPH_VERSION, "contract": self.s.contract_version},
        }
        # follow-up: carry fresh evidence from the previous run in the same conversation
        conv = state.get("conversation_id")
        if conv and (req.resume_from_request_id or resume_state is None):
            prev = await self.repo.latest_state_for_conversation(conv, exclude=rid)
            snap = (prev or {}).get("snapshot") or {}
            # evidence may only be carried over for the same trip *and* revision (apply-route bumps the revision)
            if prev and snap.get("trip_id") == str(tr.trip_id) and snap.get("trip_revision") == tr.trip_revision:
                state["snapshot"] = prev["snapshot"]
                state["snapshot_id"] = prev.get("snapshot_id")
                state["snapshot_created_at"] = prev.get("snapshot_created_at")
                state["previous_recommendation_id"] = prev.get("recommendation_id")
        self.states[rid] = state
        await self.progress.accepted(tr.request_id)
        await self._save(state)
        self.tasks[rid] = asyncio.create_task(self._execute(state))
        return RunRef(
            request_id=tr.request_id,
            status=RunStatus.QUEUED,
            events_url=f"/api/v1/runs/{rid}/events",
            poll_url=f"/api/v1/runs/{rid}",
            submitted_at=now,
            conversation_id=UUID(state["conversation_id"]) if state["conversation_id"] else None,
        )

    async def cancel(self, rid: str, reason: str) -> RunState | None:
        await self.progress.cancel(rid)
        task = self.tasks.get(rid)
        if task and not task.done():
            task.cancel()
        state = self.states.get(rid)
        if state and state["status"] in (RunStatus.QUEUED.value, RunStatus.RUNNING.value, RunStatus.NEEDS_INPUT.value):
            state["status"] = RunStatus.CANCELLED.value
            state["errors"].append({"code": "CANCELLED", "message": reason[:120]})
            await self.progress.failed(
                UUID(rid), "CANCELLED", "run cancelled by request", False, status=RunStatus.CANCELLED
            )
            await self._save(state)
        return await self.progress.load_state(rid)

    async def get(self, rid: str) -> RunState | None:
        return await self.progress.load_state(rid)

    # ------------------------------------------------------------------ execution
    async def _execute(self, state: AgentState) -> None:
        rid = state["request_id"]
        set_context(
            RequestContext(
                request_id=rid, correlation_id=state["correlation_id"], contract_version=self.s.contract_version
            )
        )
        budget = Budget(
            max_tool_calls=self.s.max_tool_calls,
            max_steps=self.s.max_agent_steps,
            deadline=time.monotonic() + self.s.agent_total_timeout_seconds,
            max_cost=self.s.max_estimated_cost_usd * 100,
        )
        tools = ToolRunner(self.clients, budget, lambda: state.get("current_stage"))
        ctx = NodeContext(tools, self.progress, self.s)
        graph = build_graph(ctx, self.checkpointer)
        state["status"] = RunStatus.RUNNING.value
        await self._save(state)
        config = {"configurable": {"thread_id": state["conversation_id"] or rid, "checkpoint_ns": rid}}
        try:
            final: AgentState = await asyncio.wait_for(
                graph.ainvoke(state, config=config), timeout=self.s.agent_total_timeout_seconds + 2
            )
            final["step_count"] = budget.steps
            final["tool_call_count"] = budget.tool_calls
            self.states[rid] = final
            if final.get("recommendation_id"):
                await self.progress.completed(UUID(rid), UUID(final["recommendation_id"]), RunStatus(final["status"]))
            elif final.get("status") == RunStatus.NEEDS_INPUT.value:
                pass  # needs_input event already published
            elif final.get("intent") == Intent.EMERGENCY.value:
                final["status"] = RunStatus.COMPLETED.value
                await self.progress.publish(
                    _event(rid, "run.completed", RunStatus.COMPLETED, message_key="emergency.shortcut")
                )
            await self._save(final, budget)
        except asyncio.CancelledError:
            state["status"] = RunStatus.CANCELLED.value
            await self._save(state, budget)
        except AppError as exc:
            if exc.code == ErrorCode.CONFLICT and "cancel" in exc.message:
                state["status"] = RunStatus.CANCELLED.value
            else:
                state["status"] = RunStatus.FAILED.value
                state["errors"].append({"code": exc.code.value, "message": exc.message[:200]})
                await self.progress.failed(UUID(rid), exc.code.value, exc.message, exc.retryable)
                log.warning(
                    "run_failed",
                    request_id=rid,
                    error_code=exc.code.value,
                    reason=exc.message[:200],
                    stage=state.get("current_stage"),
                )
            await self._save(state, budget)
        except TimeoutError:
            state["status"] = RunStatus.FAILED.value
            state["errors"].append({"code": "DEPENDENCY_TIMEOUT", "message": "agent total timeout"})
            await self.progress.failed(UUID(rid), "DEPENDENCY_TIMEOUT", "assessment exceeded the time budget", True)
            await self._save(state, budget)
        except Exception as exc:  # noqa: BLE001 - never leak; stable failure
            state["status"] = RunStatus.FAILED.value
            state["errors"].append({"code": "INTERNAL_ERROR", "message": type(exc).__name__})
            await self.progress.failed(UUID(rid), "INTERNAL_ERROR", "unexpected error during assessment", False)
            log.exception("run_crashed", request_id=rid, stage=state.get("current_stage"))
            await self._save(state, budget)
        finally:
            await self.repo.save_tool_calls(rid, tools.records)

    async def _save(self, state: AgentState, budget: Budget | None = None) -> None:
        v = state.get("versions", {})
        rs = RunState(
            request_id=UUID(state["request_id"]),
            trip_id=UUID(state["trip_id"]),
            conversation_id=UUID(state["conversation_id"]) if state.get("conversation_id") else None,
            status=RunStatus(state["status"]),
            stage=RunStage(state["current_stage"]) if state.get("current_stage") else None,  # type: ignore[arg-type]
            intent=Intent(state["intent"]) if state.get("intent") else None,  # type: ignore[arg-type]
            missing_fields=state.get("missing_fields", []),
            recommendation_id=UUID(state["recommendation_id"]) if state.get("recommendation_id") else None,
            snapshot_id=UUID(state["snapshot_id"]) if state.get("snapshot_id") else None,
            decision_id=UUID(state["decision_id"]) if state.get("decision_id") else None,
            degraded_services=state.get("degraded_services", []),
            error_code=state["errors"][-1]["code"] if state.get("errors") else None,
            error_message=state["errors"][-1]["message"] if state.get("errors") else None,
            step_count=budget.steps if budget else state.get("step_count", 0),
            tool_call_count=budget.tool_calls if budget else state.get("tool_call_count", 0),
            llm_call_count=state.get("llm_call_count", 0),
            versions=VersionInfo(
                policy=v.get("policy"),
                prompt=v.get("prompt"),
                llm_model=v.get("llm_model"),
                contract=self.s.contract_version,
                graph=f"{GRAPH_VERSION}+{graph_checksum()}",
                model=v.get("model"),
                feature_schema=v.get("feature_schema"),
                knowledge_collection=v.get("knowledge_collection"),
            ),
            started_at=datetime.fromisoformat(state["started_at"]),
            updated_at=datetime.now(UTC),
            completed_at=datetime.now(UTC)
            if state["status"]
            in (RunStatus.COMPLETED.value, RunStatus.PARTIAL.value, RunStatus.FAILED.value, RunStatus.CANCELLED.value)
            else None,
        )
        await self.progress.save_state(rs)
        await self.repo.save_run(rs, dict(state))


def _event(rid: str, event_type: Any, status: RunStatus, *, message_key: str) -> Any:
    from sta_contracts.models import RunProgressEvent

    return RunProgressEvent(
        event_id=int(time.time() * 1000) % 1_000_000,
        event_type=event_type,
        request_id=UUID(rid),
        status=status,
        message_key=message_key,
        percent=100,
        occurred_at=datetime.now(UTC),
    )
