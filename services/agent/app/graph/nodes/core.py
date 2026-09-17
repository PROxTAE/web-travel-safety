"""Graph nodes. Each node: bump step budget, check cancellation, publish stage, call at most one allowlisted tool,
store references in state. No node decides risk or action; that is Module 06/07's job."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sta_common.errors import AppError, ErrorCode
from sta_contracts.enums import Intent, QualityGate, RunStage, RunStatus
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
    TravelRequest,
)

from app.graph.intent import classify, missing_critical, needs_fresh_data
from app.graph.state import AgentState
from app.progress.publisher import ProgressPublisher
from app.tools.registry import ToolRunner


class NodeContext:
    def __init__(self, tools: ToolRunner, progress: ProgressPublisher, settings: Any) -> None:
        self.tools = tools
        self.progress = progress
        self.settings = settings

    async def _enter(self, state: AgentState, stage: RunStage) -> None:
        self.tools.budget.step()
        if await self.progress.is_cancelled(state["request_id"]):
            raise AppError(ErrorCode.CONFLICT, "run cancelled", status_code=409)
        state["current_stage"] = stage.value
        await self.progress.stage(UUID(state["request_id"]), stage)

    async def _degraded(self, state: AgentState, items: list[str]) -> None:
        for d in items:
            if d not in state["degraded_services"]:
                state["degraded_services"].append(d)
                svc, _, reason = d.partition(":")
                await self.progress.degraded(UUID(state["request_id"]), svc.strip(), reason.strip() or "degraded")

    # ------------------------------------------------------------------ nodes
    async def validate_input(self, state: AgentState) -> AgentState:
        await self._enter(state, RunStage.VALIDATING)
        req = TravelRequest.model_validate(state["travel_request"])  # contract validation (raises on violation)
        state["missing_fields"] = missing_critical(req)
        return state

    async def classify_intent(self, state: AgentState) -> AgentState:
        req = TravelRequest.model_validate(state["travel_request"])
        res = classify(
            req,
            is_followup=state.get("conversation_id") is not None
            and state.get("previous_recommendation_id") is not None,
        )
        state["intent"] = res.intent.value
        if res.injection_flagged:
            state["limitations"].append("USER_TEXT_FLAGGED_AS_INSTRUCTION_ATTEMPT")
        state["reuse_snapshot"] = (
            bool(state.get("snapshot"))
            and not needs_fresh_data(res.intent, req.question)
            and _fresh(state.get("snapshot_created_at"), self.settings.followup_reuse_max_age_seconds)
        )
        return state

    async def check_required_fields(self, state: AgentState) -> AgentState:
        if state["intent"] == Intent.EMERGENCY.value:
            state["status"] = RunStatus.COMPLETED.value
            state["limitations"].append("EMERGENCY_SHORTCUT: open the Emergency Center; no automatic contact is made")
            return state
        if state["missing_fields"]:
            state["status"] = RunStatus.NEEDS_INPUT.value
            await self.progress.needs_input(UUID(state["request_id"]), state["missing_fields"])
        return state

    async def fetch_external_data(self, state: AgentState) -> AgentState:
        await self._enter(state, RunStage.FETCHING_EXTERNAL_DATA)
        req = TravelRequest.model_validate(state["travel_request"])
        q = ContextQuery(
            request_id=req.request_id,
            trip_id=req.trip_id,
            origin=req.origin,
            destination=req.destination,
            departure_time=req.departure_time,
            travel_modes=req.travel_modes,
            preferences=req.preferences,
            locale=req.locale,
            avoid_geometries=req.avoid_geometries,
        )
        out, degraded = await self.tools.call("external_data.query_context", q, stage=RunStage.FETCHING_EXTERNAL_DATA)
        ctx = out if isinstance(out, ExternalContext) else ExternalContext.model_validate(out)
        state["external_context"] = ctx.model_dump(mode="json")
        await self._degraded(state, list(dict.fromkeys([*degraded, *ctx.degraded_services])))
        for u in ctx.unavailable_capabilities:
            if u not in state["limitations"]:
                state["limitations"].append(f"UNAVAILABLE_CAPABILITY:{u}")
        return state

    async def integrate_data(self, state: AgentState) -> AgentState:
        await self._enter(state, RunStage.INTEGRATING_DATA)
        req = TravelRequest.model_validate(state["travel_request"])
        ctx = ExternalContext.model_validate(state["external_context"])
        out, degraded = await self.tools.call(
            "data_integration.create_snapshot",
            SnapshotCreateRequest(travel_request=req, external_context=ctx),
            stage=RunStage.INTEGRATING_DATA,
        )
        snap = out if isinstance(out, IntegratedTravelContext) else IntegratedTravelContext.model_validate(out)
        if snap.request_id != req.request_id or snap.trip_id != req.trip_id or snap.trip_revision != req.trip_revision:
            raise AppError(ErrorCode.POLICY_VALIDATION_FAILED, "snapshot does not match the request/trip revision")
        state["snapshot"] = snap.model_dump(mode="json")
        state["snapshot_id"] = str(snap.snapshot_id)
        state["snapshot_created_at"] = snap.created_at.isoformat()
        state["quality_gate"] = snap.quality_summary.gate.value
        state["versions"]["feature_schema"] = snap.feature_schema_version
        await self._degraded(state, degraded)
        if snap.quality_summary.gate == QualityGate.BLOCK:
            state["limitations"].append("DATA_QUALITY_BLOCK:" + ",".join(snap.quality_summary.reasons))
        return state

    async def build_evidence(self, state: AgentState) -> AgentState:
        await self._enter(state, RunStage.ASSESSING_RISK)
        req = TravelRequest.model_validate(state["travel_request"])
        snap = IntegratedTravelContext.model_validate(state["snapshot"])
        payload = EvidencePackageRequest(
            snapshot=snap,
            locale=req.locale,
            question=req.question,
            country_code=req.destination.country_code,
            request_id=req.request_id,  # a reused snapshot still yields a package owned by *this* request
        )
        await self.progress.stage(req.request_id, RunStage.RETRIEVING_GUIDANCE)
        await self.progress.stage(req.request_id, RunStage.EVALUATING_ROUTES)
        out, degraded = await self.tools.call(
            "risk_knowledge.build_evidence_package", payload, stage=RunStage.ASSESSING_RISK
        )
        pkg = out if isinstance(out, EvidencePackage) else EvidencePackage.model_validate(out)
        if pkg.snapshot_id != snap.snapshot_id or pkg.request_id != req.request_id:
            raise AppError(ErrorCode.POLICY_VALIDATION_FAILED, "evidence package does not match the snapshot")
        state["evidence_package"] = pkg.model_dump(mode="json")
        state["versions"]["knowledge_collection"] = pkg.knowledge_collection_version
        state["versions"]["model"] = ";".join(
            sorted({f"{a.model.name}@{a.model.version}" for a in pkg.risk_assessments})
        )
        await self._degraded(state, list(dict.fromkeys([*degraded, *pkg.degraded_services])))
        for lim in pkg.limitations:
            if lim not in state["limitations"]:
                state["limitations"].append(lim)
        return state

    async def make_decision(self, state: AgentState) -> AgentState:
        await self._enter(state, RunStage.MAKING_DECISION)
        req = TravelRequest.model_validate(state["travel_request"])
        pkg = EvidencePackage.model_validate(state["evidence_package"])
        await self.progress.stage(req.request_id, RunStage.EXPLAINING)
        out, degraded = await self.tools.call(
            "decision_engine.create_decision",
            DecisionRequest(travel_request=req, evidence_package=pkg, locale=req.locale, llm_enabled=True),
            stage=RunStage.MAKING_DECISION,
        )
        dec = out if isinstance(out, DecisionResult) else DecisionResult.model_validate(out)
        if dec.request_id != req.request_id or dec.snapshot_id != pkg.snapshot_id:
            raise AppError(ErrorCode.POLICY_VALIDATION_FAILED, "decision does not match the evidence package")
        state["decision"] = dec.model_dump(mode="json")
        state["decision_id"] = str(dec.decision_id)
        state["versions"]["policy"] = dec.versions.policy
        state["versions"]["prompt"] = dec.versions.prompt
        state["versions"]["llm_model"] = dec.versions.llm_model
        await self._degraded(state, degraded)
        return state

    async def format_recommendation(self, state: AgentState) -> AgentState:
        await self._enter(state, RunStage.FORMATTING_RESPONSE)
        req = TravelRequest.model_validate(state["travel_request"])
        pkg = EvidencePackage.model_validate(state["evidence_package"])
        dec = DecisionResult.model_validate(state["decision"])
        snap = IntegratedTravelContext.model_validate(state["snapshot"])
        sources = list({w.source.source_id: w.source for w in snap.weather}.values())[:20]
        weather_summary = {
            k: snap.features.get(k)
            for k in (
                "weather_max_precip_mm",
                "weather_max_wind_gust_kmh",
                "weather_max_temperature_c",
                "weather_min_temperature_c",
                "weather_coverage_ratio",
            )
        }
        transport_summary = {
            "records": len(snap.transport),
            "statuses": sorted({t.status.value for t in snap.transport}),
        }
        payload = RecommendationCreateRequest(
            travel_request=req,
            decision=dec,
            evidence_package=pkg,
            snapshot_created_at=snap.created_at,
            snapshot_sources=sources,
            weather_summary=weather_summary,
            transport_summary=transport_summary,
            previous_recommendation_id=UUID(state["previous_recommendation_id"])
            if state.get("previous_recommendation_id")
            else None,
        )
        out, degraded = await self.tools.call(
            "recommendation.create_recommendation", payload, stage=RunStage.FORMATTING_RESPONSE
        )
        rec = out if isinstance(out, RecommendationResponse) else RecommendationResponse.model_validate(out)
        # final contract checks: same request, locked action preserved, no action drift
        if rec.request_id != req.request_id or rec.action_code != dec.action_code or rec.decision_id != dec.decision_id:
            raise AppError(ErrorCode.POLICY_VALIDATION_FAILED, "final recommendation violates the locked decision")
        state["recommendation_id"] = str(rec.recommendation_id)
        state["status"] = (
            RunStatus.PARTIAL.value
            if (rec.status == RunStatus.PARTIAL or state["degraded_services"])
            else RunStatus.COMPLETED.value
        )
        await self._degraded(state, degraded)
        return state


def _fresh(created_at: str | None, max_age: int) -> bool:
    if not created_at:
        return False
    age = (datetime.now(UTC) - datetime.fromisoformat(created_at)).total_seconds()
    return age <= max_age
