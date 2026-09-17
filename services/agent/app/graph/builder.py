"""Finite LangGraph StateGraph (03 plan §Graph design). No cycles; every path reaches END within max steps."""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from sta_contracts.enums import Intent, RunStatus

from app.graph.nodes.core import NodeContext
from app.graph.state import AgentState

GRAPH_VERSION = "1.0.0"


def _after_required(state: AgentState) -> Literal["fetch_external_data", "build_evidence", "end"]:
    if state.get("status") in (RunStatus.NEEDS_INPUT.value, RunStatus.COMPLETED.value) and not state.get(
        "recommendation_id"
    ):
        return "end"
    if state.get("intent") == Intent.EMERGENCY.value:
        return "end"
    if state.get("reuse_snapshot"):
        return "build_evidence"  # informational follow-up on fresh evidence: skip provider fetch + integration
    return "fetch_external_data"


def build_graph(ctx: NodeContext, checkpointer: Any = None) -> Any:
    g: StateGraph = StateGraph(AgentState)
    g.add_node("validate_input", ctx.validate_input)
    g.add_node("classify_intent", ctx.classify_intent)
    g.add_node("check_required_fields", ctx.check_required_fields)
    g.add_node("fetch_external_data", ctx.fetch_external_data)
    g.add_node("integrate_data", ctx.integrate_data)
    g.add_node("build_evidence", ctx.build_evidence)
    g.add_node("make_decision", ctx.make_decision)
    g.add_node("format_recommendation", ctx.format_recommendation)
    g.add_edge(START, "validate_input")
    g.add_edge("validate_input", "classify_intent")
    g.add_edge("classify_intent", "check_required_fields")
    g.add_conditional_edges(
        "check_required_fields",
        _after_required,
        {"fetch_external_data": "fetch_external_data", "build_evidence": "build_evidence", "end": END},
    )
    g.add_edge("fetch_external_data", "integrate_data")
    g.add_edge("integrate_data", "build_evidence")
    g.add_edge("build_evidence", "make_decision")
    g.add_edge("make_decision", "format_recommendation")
    g.add_edge("format_recommendation", END)
    return g.compile(checkpointer=checkpointer)


def graph_checksum() -> str:
    """Checksum of the graph topology (node names + edges) so runs record which graph produced them."""
    topo = (
        "validate_input>classify_intent>check_required_fields>{fetch_external_data|build_evidence|END}"
        ">integrate_data>build_evidence>make_decision>format_recommendation>END"
    )
    return hashlib.sha256((GRAPH_VERSION + topo).encode()).hexdigest()[:16]
