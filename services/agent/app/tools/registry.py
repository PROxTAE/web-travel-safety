"""Allowlisted tool registry (03 plan §Tool registry rules).

Every tool declares: stable name@version, input/output contract models, allowed stages, base URL from
config (never from user/model), timeouts, retryable codes, budget weight and redaction. The LLM never sees
generic HTTP/shell/filesystem tools — it cannot call tools at all; only graph nodes can.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from sta_common.errors import AppError, ErrorCode
from sta_common.http import ResilientClient
from sta_common.logging import get_logger
from sta_contracts.enums import RunStage
from sta_contracts.models import (
    ContextQuery,
    DecisionRequest,
    DecisionResult,
    EvidencePackage,
    EvidencePackageRequest,
    ExternalContext,
    IntegratedTravelContext,
    RecommendationCreateRequest,
    RecommendationResponse,
    SnapshotCreateRequest,
)

log = get_logger("tools")


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    version: str
    path: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    allowed_stages: tuple[RunStage, ...]
    timeout_seconds: float
    max_attempts: int
    cost_weight: float
    idempotent: bool
    consumer: str  # dependency name for metrics

    @property
    def full_name(self) -> str:
        return f"{self.name}@{self.version}"


TOOLS: dict[str, ToolSpec] = {
    "external_data.query_context": ToolSpec(
        "external_data.query_context",
        "1",
        "/internal/v1/context/query",
        ContextQuery,
        ExternalContext,
        (RunStage.FETCHING_EXTERNAL_DATA,),
        22.0,
        1,
        1.0,
        True,
        "external-data",
    ),
    "data_integration.create_snapshot": ToolSpec(
        "data_integration.create_snapshot",
        "1",
        "/internal/v1/snapshots",
        SnapshotCreateRequest,
        IntegratedTravelContext,
        (RunStage.INTEGRATING_DATA,),
        12.0,
        2,
        0.5,
        True,
        "data-integration",
    ),
    "risk_knowledge.build_evidence_package": ToolSpec(
        "risk_knowledge.build_evidence_package",
        "1",
        "/internal/v1/evidence/package",
        EvidencePackageRequest,
        EvidencePackage,
        (RunStage.ASSESSING_RISK, RunStage.RETRIEVING_GUIDANCE, RunStage.EVALUATING_ROUTES),
        12.0,
        2,
        1.0,
        True,
        "risk-knowledge",
    ),
    "decision_engine.create_decision": ToolSpec(
        "decision_engine.create_decision",
        "1",
        "/internal/v1/decisions",
        DecisionRequest,
        DecisionResult,
        (RunStage.MAKING_DECISION, RunStage.EXPLAINING),
        20.0,
        1,
        1.0,
        False,
        "decision-engine",
    ),
    "recommendation.create_recommendation": ToolSpec(
        "recommendation.create_recommendation",
        "1",
        "/internal/v1/recommendations",
        RecommendationCreateRequest,
        RecommendationResponse,
        (RunStage.FORMATTING_RESPONSE,),
        8.0,
        2,
        0.5,
        True,
        "recommendation",
    ),
}


@dataclass(slots=True)
class ToolCallRecord:
    tool: str
    input_hash: str
    status: str
    duration_ms: float
    error_code: str | None = None
    degraded: list[str] = field(default_factory=list)


class Budget:
    def __init__(self, *, max_tool_calls: int, max_steps: int, deadline: float, max_cost: float) -> None:
        self.max_tool_calls = max_tool_calls
        self.max_steps = max_steps
        self.deadline = deadline
        self.max_cost = max_cost
        self.tool_calls = 0
        self.steps = 0
        self.cost = 0.0

    def remaining_seconds(self) -> float:
        return self.deadline - time.monotonic()

    def check_tool(self, spec: ToolSpec) -> None:
        if self.tool_calls >= self.max_tool_calls:
            raise AppError(ErrorCode.POLICY_VALIDATION_FAILED, "agent tool budget exhausted")
        if self.cost + spec.cost_weight > self.max_cost:
            raise AppError(ErrorCode.POLICY_VALIDATION_FAILED, "agent cost budget exhausted")
        if self.remaining_seconds() <= 0:
            raise AppError(ErrorCode.DEPENDENCY_TIMEOUT, "agent total deadline exceeded")

    def step(self) -> None:
        self.steps += 1
        if self.steps > self.max_steps:
            raise AppError(ErrorCode.POLICY_VALIDATION_FAILED, "agent step budget exhausted")


class ToolRunner:
    """Executes allowlisted tools against configured base URLs with typed IO, budget and audit."""

    def __init__(self, clients: dict[str, ResilientClient], budget: Budget, stage_getter: Any) -> None:
        self.clients = clients
        self.budget = budget
        self.stage_getter = stage_getter
        self.records: list[ToolCallRecord] = []

    async def call(
        self, name: str, payload: BaseModel, *, stage: RunStage, headers: dict[str, str] | None = None
    ) -> tuple[BaseModel, list[str]]:
        spec = TOOLS[name]
        if stage not in spec.allowed_stages:
            raise AppError(
                ErrorCode.POLICY_VALIDATION_FAILED, f"tool {spec.full_name} not allowed in stage {stage.value}"
            )
        if not isinstance(payload, spec.input_model):
            raise AppError(ErrorCode.POLICY_VALIDATION_FAILED, f"tool {spec.full_name} input type mismatch")
        self.budget.check_tool(spec)
        self.budget.tool_calls += 1
        self.budget.cost += spec.cost_weight
        body = payload.model_dump(mode="json")
        input_hash = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
        client = self.clients[spec.consumer]
        started = time.perf_counter()
        deadline = min(spec.timeout_seconds, max(1.0, self.budget.remaining_seconds()))
        try:
            resp = await client.request(
                "POST", spec.path, json=body, deadline_seconds=deadline, headers=headers, idempotent=spec.idempotent
            )
            data = client.raise_for_envelope(resp)
        except AppError as exc:
            self.records.append(
                ToolCallRecord(
                    spec.full_name, input_hash, "error", (time.perf_counter() - started) * 1000, exc.code.value
                )
            )
            raise
        degraded = list((data or {}).get("meta", {}).get("degraded_services", [])) if isinstance(data, dict) else []
        try:
            out = spec.output_model.model_validate(data["data"])
        except Exception as exc:  # noqa: BLE001 - malformed downstream response is a stable dependency error
            self.records.append(
                ToolCallRecord(
                    spec.full_name,
                    input_hash,
                    "malformed",
                    (time.perf_counter() - started) * 1000,
                    "MALFORMED_RESPONSE",
                )
            )
            raise AppError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, f"{spec.consumer} returned a response that violates the contract"
            ) from exc
        self.records.append(
            ToolCallRecord(spec.full_name, input_hash, "ok", (time.perf_counter() - started) * 1000, None, degraded)
        )
        return out, degraded
