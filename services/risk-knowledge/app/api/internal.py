"""Internal API — 00_API_AND_DATA_CONTRACTS §5.4"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sta_common.envelope import ok
from sta_common.errors import AppError, ErrorCode
from sta_common.metrics import DEGRADED_RESULTS
from sta_contracts.enums import DisasterEventType, QualityGate, ReasonCode, RiskLevel
from sta_contracts.models import EvidencePackage, EvidencePackageRequest, IntegratedTravelContext, TravelPreference

from app.knowledge.retrieve import build_query, fact_strings, hazards_in_scope
from app.risk.inference import assess_all
from app.risk.thresholds import RISK_RANK
from app.routes.ranking import RANKING_VERSION, rank_routes
from app.settings import Settings


class AssessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot: IntegratedTravelContext
    route_ids: list[UUID] | None = None


class RetrieveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hazards: list[DisasterEventType] = Field(default_factory=list)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    risk_level: RiskLevel = RiskLevel.UNKNOWN
    locale: str = "en-US"
    question: str | None = Field(default=None, max_length=500)
    top_k: int | None = Field(default=None, ge=1, le=10)


class EvaluateRoutesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot: IntegratedTravelContext
    preferences: TravelPreference = Field(default_factory=TravelPreference)


def build_router(settings: Settings, auth_dep: Any) -> APIRouter:
    r = APIRouter(prefix="/internal/v1", dependencies=[Depends(auth_dep)], tags=["internal"])
    cv = settings.contract_version

    def st(request: Request) -> Any:
        return request.app.state

    def _assess(request: Request, snapshot: IntegratedTravelContext, route_ids: list[UUID] | None):  # type: ignore[no-untyped-def]
        s = st(request)
        return assess_all(
            snapshot,
            route_ids,
            model=s.model,
            thresholds=s.thresholds,
            overrides=s.overrides,
            feature_schema_version=s.feature_schema_version,
        )

    @r.post("/risk/assess")
    async def risk_assess(body: AssessRequest, request: Request) -> dict[str, Any]:
        if body.snapshot.feature_schema_version != st(request).feature_schema_version:
            raise AppError(ErrorCode.POLICY_VALIDATION_FAILED, "feature schema version mismatch")
        out = _assess(request, body.snapshot, body.route_ids)
        degraded = ["risk-knowledge: model unavailable (rule baseline)"] if st(request).model is None else []
        if degraded:
            DEGRADED_RESULTS.labels(settings.service_name, "model_unavailable").inc()
        return ok([a.model_dump(mode="json") for a in out], cv, degraded_services=degraded)

    @r.post("/knowledge/retrieve")
    async def knowledge_retrieve(body: RetrieveRequest, request: Request) -> dict[str, Any]:
        idx = st(request).index
        q = build_query(body.hazards, body.risk_level.value, body.locale, body.question)
        ev = await asyncio.to_thread(
            idx.retrieve,
            query=q,
            hazards=body.hazards,
            country=body.country_code,
            language=body.locale,
            top_k=body.top_k or settings.retrieval_top_k,
            min_score=settings.retrieval_min_score,
        )
        limitations = [] if ev else [ReasonCode.NO_RELIABLE_KNOWLEDGE_EVIDENCE.value]
        return ok({"evidence": [e.model_dump(mode="json") for e in ev], "limitations": limitations, "query": q}, cv)

    @r.post("/routes/evaluate")
    async def routes_evaluate(body: EvaluateRoutesRequest, request: Request) -> dict[str, Any]:
        assessments = _assess(request, body.snapshot, None)
        ranked = rank_routes(
            body.snapshot.route_candidates,
            assessments,
            body.preferences,
            materially_safer_delta=settings.materially_safer_delta,
            max_extra_duration_ratio=settings.max_extra_duration_ratio,
        )
        return ok(
            {
                "routes": [x.model_dump(mode="json") for x in ranked.routes],
                "recommended_route_id": ranked.recommended_id,
                "materially_safer_available": ranked.materially_safer_available,
                "reasons": ranked.reasons,
                "ranking_version": RANKING_VERSION,
            },
            cv,
        )

    @r.post("/evidence/package")
    async def evidence_package(body: EvidencePackageRequest, request: Request) -> dict[str, Any]:
        s = st(request)
        snap = body.snapshot
        if snap.feature_schema_version != s.feature_schema_version:
            raise AppError(ErrorCode.POLICY_VALIDATION_FAILED, "feature schema version mismatch")
        if snap.quality_summary.gate == QualityGate.BLOCK and any(r.usable for r in snap.route_candidates):
            raise AppError(ErrorCode.INSUFFICIENT_EVIDENCE, "snapshot quality gate is BLOCK")
        assessments = _assess(request, snap, None)
        ranked = rank_routes(
            snap.route_candidates,
            assessments,
            TravelPreference(),
            materially_safer_delta=settings.materially_safer_delta,
            max_extra_duration_ratio=settings.max_extra_duration_ratio,
        )
        worst = max((a.risk_level for a in assessments), key=lambda lvl: RISK_RANK[lvl], default=RiskLevel.UNKNOWN)
        hazards = hazards_in_scope(snap, assessments)
        country = body.country_code
        query = build_query(hazards, worst.value, body.locale, body.question)
        evidence = await asyncio.to_thread(
            s.index.retrieve,
            query=query,
            hazards=hazards,
            country=country,
            language=body.locale,
            top_k=settings.retrieval_top_k,
            min_score=settings.retrieval_min_score,
        )
        limitations: list[str] = []
        degraded: list[str] = list(snap.quality_summary.degraded_services)
        if s.model is None:
            degraded.append("risk-knowledge: model unavailable (rule baseline)")
            limitations.append(ReasonCode.MODEL_UNAVAILABLE.value)
        if not evidence:
            limitations.append(ReasonCode.NO_RELIABLE_KNOWLEDGE_EVIDENCE.value)
        if s.index.status().collection_version is None:
            degraded.append("risk-knowledge: knowledge collection not indexed")
        if snap.quality_summary.gate == QualityGate.DEGRADED:
            limitations.extend(f"DATA_QUALITY:{x}" for x in snap.quality_summary.reasons)
        if not ranked.routes or not any(x.usable for x in ranked.routes):
            limitations.append(ReasonCode.NO_SAFER_ROUTE.value)
        wx, tr, dz = fact_strings(snap)
        pkg = EvidencePackage(
            package_id=uuid4(),
            request_id=snap.request_id,
            snapshot_id=snap.snapshot_id,
            trip_revision=snap.trip_revision,
            risk_assessments=assessments,
            routes=ranked.routes,
            evidence=evidence,
            quality_summary=snap.quality_summary,
            conflict_summary=snap.conflict_summary,
            official_alerts=snap.official_alerts,
            weather_facts=wx,
            transport_facts=tr,
            disaster_facts=dz,
            limitations=sorted(set(limitations)),
            degraded_services=sorted(set(degraded)),
            knowledge_collection_version=s.index.status().collection_version,
            created_at=datetime.now(UTC),
        )
        return ok(pkg.model_dump(mode="json"), cv, degraded_services=pkg.degraded_services)

    @r.get("/models/current")
    async def models_current(request: Request) -> dict[str, Any]:
        s = st(request)
        if s.model is None:
            return ok(
                {
                    "status": "UNAVAILABLE",
                    "reason": s.model_unavailable_reason,
                    "fallback": "rule-baseline",
                    "thresholds_version": s.thresholds.version,
                },
                cv,
            )
        m = s.model
        return ok(
            {
                "status": "ACTIVE",
                "name": m.name,
                "version": m.version,
                "feature_schema_version": m.feature_schema_version,
                "thresholds_version": s.thresholds.version,
                "overrides_version": s.overrides.version,
                "checksum": m.checksum,
                "metrics": m.metrics,
                "trained_at": m.manifest.get("trained_at"),
                "approved_by": m.manifest.get("approved_by"),
            },
            cv,
        )

    @r.get("/knowledge/status")
    async def knowledge_status(request: Request) -> dict[str, Any]:
        stt = st(request).index.status()
        return ok(asdict(stt), cv)

    return r
