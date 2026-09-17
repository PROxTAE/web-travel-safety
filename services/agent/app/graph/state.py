"""AgentState — serializable TypedDict stored in checkpoints. References/metadata only; no raw provider
payloads, secrets, profile data or chain-of-thought."""

from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    # identity
    request_id: str
    correlation_id: str
    trip_id: str
    conversation_id: str | None
    user_scope_hash: str | None
    # input
    travel_request: dict[str, Any]  # TravelRequest (json)
    intent: str | None
    missing_fields: list[str]
    question: str | None
    # plan / control
    graph_version: str
    current_stage: str | None
    status: str
    step_count: int
    tool_call_count: int
    llm_call_count: int
    started_at: str
    deadline_at: str
    cancelled: bool
    errors: list[dict[str, str]]
    # observations (references + minimal summaries)
    external_context: dict[str, Any] | None
    snapshot: dict[str, Any] | None
    snapshot_id: str | None
    snapshot_created_at: str | None
    evidence_package: dict[str, Any] | None
    decision: dict[str, Any] | None
    recommendation_id: str | None
    decision_id: str | None
    # quality
    degraded_services: list[str]
    quality_gate: str | None
    limitations: list[str]
    # follow-up
    previous_recommendation_id: str | None
    reuse_snapshot: bool
    # versions
    versions: dict[str, str | None]
