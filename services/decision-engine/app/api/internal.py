"""Internal API — 00_API_AND_DATA_CONTRACTS §5.5"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sta_common.envelope import ok
from sta_common.errors import AppError, ErrorCode
from sta_contracts.models import DecisionRequest, DecisionResult, EvidencePackage

from app.domain.decision import decide
from app.policy.evaluator import ConsistencyError, check_consistency, derive_facts, facts_as_dict
from app.settings import Settings


class ValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: DecisionResult
    evidence_package: EvidencePackage


def build_router(settings: Settings, auth_dep: Any) -> APIRouter:
    r = APIRouter(prefix="/internal/v1", dependencies=[Depends(auth_dep)], tags=["internal"])
    cv = settings.contract_version

    @r.post("/decisions", status_code=201)
    async def create_decision(body: DecisionRequest, request: Request) -> dict[str, Any]:
        st = request.app.state
        try:
            outcome = await decide(
                body.travel_request,
                body.evidence_package,
                policy=st.policy,
                llm=st.llm,
                locale=body.locale,
                llm_enabled=body.llm_enabled and settings.llm_enabled,
                feature_schema_versions=settings.feature_schema_versions,
                contract_version=cv,
            )
        except ConsistencyError as exc:
            raise AppError(ErrorCode.POLICY_VALIDATION_FAILED, f"{exc.code}: {exc}") from None
        await st.audit.record(outcome.audit)
        degraded = (
            [f"decision-engine: explanation fallback ({outcome.llm.fallback_reason})"]
            if outcome.llm.used_fallback
            else []
        )
        return ok(outcome.result.model_dump(mode="json"), cv, degraded_services=degraded)

    @r.post("/decisions/validate")
    async def validate_decision(body: ValidateRequest, request: Request) -> dict[str, Any]:
        st = request.app.state
        d, pkg = body.decision, body.evidence_package
        problems: list[str] = []
        if d.snapshot_id != pkg.snapshot_id:
            problems.append("SNAPSHOT_MISMATCH")
        if d.request_id != pkg.request_id:
            problems.append("REQUEST_MISMATCH")
        allowed = {e.evidence_id for e in pkg.evidence}
        if any(c not in allowed for c in d.citations):
            problems.append("UNKNOWN_CITATION")
        if d.versions.policy != st.policy.version:
            problems.append(f"POLICY_VERSION_MISMATCH:{d.versions.policy}!={st.policy.version}")
        facts = derive_facts(pkg, st.policy)
        from app.policy.evaluator import evaluate

        ev = evaluate(pkg, st.policy)
        if ev.action != d.action_code:
            problems.append(f"ACTION_MISMATCH:policy={ev.action.value},decision={d.action_code.value}")
        if d.selected_route_id and not any(r.route_id == d.selected_route_id and r.usable for r in pkg.routes):
            problems.append("SELECTED_ROUTE_NOT_USABLE")
        return ok(
            {
                "valid": not problems,
                "problems": problems,
                "facts": facts_as_dict(facts),
                "policy_version": st.policy.version,
            },
            cv,
        )

    @r.get("/policies/current")
    async def policies_current(request: Request) -> dict[str, Any]:
        p = request.app.state.policy
        return ok(
            {
                "version": p.version,
                "checksum": p.checksum,
                "status": p.status,
                "approved_by": p.approved_by,
                "approved_at": p.approved_at,
                "thresholds": p.thresholds,
                "rules": [
                    {"id": r.id, "priority": r.priority, "action": r.action, "escalate": r.escalate} for r in p.rules
                ],
                "prompt_version": "1.0.0",
                "llm_model": settings.openai_explainer_model or None,
                "llm_enabled": request.app.state.llm.enabled,
            },
            cv,
        )

    @r.post("/decisions/preview-consistency")
    async def preview(body: DecisionRequest) -> dict[str, Any]:
        try:
            check_consistency(
                body.travel_request, body.evidence_package, feature_schema_versions=settings.feature_schema_versions
            )
        except ConsistencyError as exc:
            return ok({"consistent": False, "code": exc.code, "message": str(exc)}, cv)
        return ok({"consistent": True}, cv)

    return r
