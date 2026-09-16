"""Decision use case: consistency gate → deterministic evaluation → locked action → grounded explanation → audit."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sta_common.metrics import DECISION_ACTIONS
from sta_contracts.enums import ActionCode, RiskLevel
from sta_contracts.models import DecisionResult, DecisionValidation, EvidencePackage, TravelRequest, VersionInfo

from app.llm.client import ExplanationClient, LlmOutcome
from app.policy.evaluator import Evaluation, check_consistency, evaluate, facts_as_dict
from app.policy.loader import Policy


@dataclass(slots=True)
class DecisionOutcome:
    result: DecisionResult
    evaluation: Evaluation
    llm: LlmOutcome
    audit: dict[str, Any]


def _hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


async def decide(
    req: TravelRequest,
    pkg: EvidencePackage,
    *,
    policy: Policy,
    llm: ExplanationClient,
    locale: str,
    llm_enabled: bool,
    feature_schema_versions: set[str],
    contract_version: str,
) -> DecisionOutcome:
    check_consistency(req, pkg, feature_schema_versions=feature_schema_versions)
    ev = evaluate(pkg, policy)
    DECISION_ACTIONS.labels(ev.action.value).inc()

    # risk level reported = level of the route the traveller is being told to take (primary unless CHANGE_ROUTE)
    by_route = {a.route_id: a for a in pkg.risk_assessments}
    level = ev.facts.primary_level
    if ev.action == ActionCode.CHANGE_ROUTE and ev.selected_route_id in by_route:
        # keep the primary (original) level as the headline: the reason we change route
        level = ev.facts.primary_level
    if ev.action == ActionCode.AVOID and level == RiskLevel.LOW:
        level = RiskLevel.HIGH if ev.facts.original_closed else level

    citations = [
        {
            "id": str(e.evidence_id),
            "title": e.title,
            "section": e.section or "",
            "authority": e.authority.value,
            "passage": e.passage[:900],
        }
        for e in pkg.evidence
    ]
    facts = [*pkg.weather_facts, *pkg.transport_facts, *pkg.disaster_facts]
    routes_txt = [
        f"{'|'.join(lbl.value for lbl in r.labels) or r.label.value}: " + "; ".join(r.trade_offs) for r in pkg.routes
    ]
    selected_txt = None
    if ev.selected_route_id:
        sel = next((r for r in pkg.routes if r.route_id == ev.selected_route_id), None)
        if sel:
            lbl = "|".join(x.value for x in sel.labels) or sel.label.value
            selected_txt = f"{lbl} ({sel.duration_seconds / 3600:.1f} h, {sel.distance_m / 1000:.0f} km)"
    limitations = list(pkg.limitations)
    if ev.escalation_required:
        limitations.append("ESCALATION_REQUIRED:" + ",".join(ev.escalation_reasons))

    if llm_enabled:
        out = await llm.explain(
            action=ev.action,
            risk_level=level,
            confidence=ev.confidence,
            reason_codes=ev.reason_codes,
            facts=facts,
            routes=routes_txt,
            limitations=limitations,
            citations=citations,
            locale=locale,
            question=req.question,
            suggested_delay_minutes=ev.suggested_delay_minutes,
            selected_route=selected_txt,
        )
    else:
        from app.llm.fallback import fallback_explanation

        exp = fallback_explanation(
            action=ev.action,
            risk_level=level,
            reason_codes=ev.reason_codes,
            limitations=limitations,
            locale=locale,
            delay_minutes=ev.suggested_delay_minutes,
        )
        out = LlmOutcome(exp, True, "llm_disabled_by_request", None, None, "1.0.0", "", None, 0.0, None, None, 0)

    # final invariant: the explanation can never carry a different action than the lock
    assert out.explanation.action_code == ev.action.value
    used_citations = [e.evidence_id for e in pkg.evidence if str(e.evidence_id) in set(out.explanation.citations_used)]
    validation = DecisionValidation(
        schema_valid=True,
        citations=out.validation.citations_ok if out.validation else True,
        locked_action=True,
        numbers=out.validation.numbers_ok if out.validation else True,
        banned_phrases=out.validation.banned_ok if out.validation else True,
        used_fallback=out.used_fallback,
        fallback_reason=out.fallback_reason,
    )
    now = datetime.now(UTC)
    versions = VersionInfo(
        policy=policy.version,
        prompt=out.prompt_version,
        llm_model=out.model,
        contract=contract_version,
        model=";".join(sorted({f"{a.model.name}@{a.model.version}" for a in pkg.risk_assessments})),
        feature_schema=next(iter({a.model.feature_schema_version for a in pkg.risk_assessments}), None),
        knowledge_collection=pkg.knowledge_collection_version,
    )
    result = DecisionResult(
        decision_id=uuid4(),
        request_id=req.request_id,
        snapshot_id=pkg.snapshot_id,
        action_code=ev.action,
        risk_level=level,
        confidence=ev.confidence,
        selected_route_id=ev.selected_route_id,
        rules_fired=[ev.rule.id],
        reason_codes=ev.reason_codes,
        escalation_required=ev.escalation_required,
        escalation_reasons=ev.escalation_reasons,
        summary=out.explanation.short_summary[:600],
        reasons=out.explanation.reasons[:8],
        immediate_actions=out.explanation.immediate_actions[:8],
        citations=used_citations,
        limitations=(out.explanation.limitations + [x for x in limitations if x not in out.explanation.limitations])[
            :12
        ],
        suggested_delay_minutes=ev.suggested_delay_minutes,
        versions=versions,
        validation=validation,
        locale=locale,
        created_at=now,
    )
    audit = {
        "decision_id": str(result.decision_id),
        "request_id": str(req.request_id),
        "input_hashes": {
            "travel_request": _hash(req.model_dump(mode="json")),
            "evidence_package": _hash(pkg.model_dump(mode="json")),
        },
        "policy": {"version": policy.version, "checksum": policy.checksum},
        "prompt": {"version": out.prompt_version, "hash": out.prompt_hash},
        "llm": {
            "model": out.model,
            "latency_ms": round(out.latency_ms, 1),
            "tokens_in": out.tokens_in,
            "tokens_out": out.tokens_out,
            "attempts": out.attempts,
            "output_hash": out.output_hash,
        },
        "facts": facts_as_dict(ev.facts),
        "rules_evaluated": ev.rules_evaluated,
        "rule_fired": ev.rule.id,
        "locked_action": ev.action.value,
        "confidence": ev.confidence,
        "escalation": ev.escalation_reasons,
        "evidence_ids": [str(e.evidence_id) for e in pkg.evidence],
        "validation": validation.model_dump(),
        "validator_problems": (out.validation.problems if out.validation else []),
        "versions": versions.model_dump(),
        "created_at": now.isoformat(),
    }
    return DecisionOutcome(result=result, evaluation=ev, llm=out, audit=audit)
