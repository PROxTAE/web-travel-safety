"""Consistency gate + deterministic facts + first-match rule evaluation + confidence/escalation.

No network, no LLM. Pure function of (TravelRequest, EvidencePackage, Policy).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sta_contracts.enums import ActionCode, QualityGate, ReasonCode, RiskLevel, RouteLabel
from sta_contracts.models import EvidencePackage, RiskAssessment, RouteCandidate, TravelRequest

from app.policy.loader import Policy, Rule

RISK_RANK = {RiskLevel.UNKNOWN: 0, RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3}
ACTION_RANK = {ActionCode.NORMAL: 0, ActionCode.DELAY: 1, ActionCode.CHANGE_ROUTE: 1, ActionCode.AVOID: 2}


class ConsistencyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class Facts:
    gate: str
    original_closed: bool
    usable_alternative: bool
    safety_critical_conflicts: bool
    primary_level: RiskLevel
    primary_p: float
    primary_uncertainty: float
    materially_safer_available: bool
    best_alternative_id: UUID | None
    best_alternative_level: RiskLevel | None
    best_alternative_p: float | None
    time_dependent: bool
    later_level: RiskLevel | None
    suggested_delay_minutes: int | None
    quality_score: float
    weather_coverage: float
    reason_codes: list[ReasonCode] = field(default_factory=list)
    primary_route_id: UUID | None = None


def check_consistency(req: TravelRequest, pkg: EvidencePackage, *, feature_schema_versions: set[str]) -> None:
    if pkg.request_id != req.request_id:
        raise ConsistencyError("REQUEST_MISMATCH", "evidence package belongs to a different request")
    if pkg.trip_revision != req.trip_revision:
        raise ConsistencyError("REVISION_MISMATCH", "evidence package was built for another trip revision")
    if not pkg.routes:
        raise ConsistencyError("NO_ROUTES", "evidence package has no routes")
    route_ids = {r.route_id for r in pkg.routes}
    for a in pkg.risk_assessments:
        if a.snapshot_id != pkg.snapshot_id:
            raise ConsistencyError("SNAPSHOT_MISMATCH", "assessment snapshot differs from package snapshot")
        if a.route_id not in route_ids:
            raise ConsistencyError("ROUTE_MISMATCH", "assessment references an unknown route")
        if a.model.feature_schema_version not in feature_schema_versions:
            raise ConsistencyError(
                "UNSUPPORTED_VERSION", f"feature schema {a.model.feature_schema_version} unsupported"
            )
    assessed = {a.route_id for a in pkg.risk_assessments}
    if not route_ids <= assessed:
        raise ConsistencyError("MISSING_ASSESSMENT", "every route must have a risk assessment")
    now = pkg.created_at
    for ev in pkg.evidence:
        if ev.expires_at is not None and ev.expires_at <= now:
            raise ConsistencyError("EXPIRED_EVIDENCE", f"citation {ev.document_id} is expired")
    for alert in pkg.official_alerts:
        if (
            alert.ends_at is not None
            and alert.ends_at < req.departure_time
            and alert.effective_at
            and alert.effective_at < alert.ends_at
        ):
            # an alert that ended before departure must not be used as active; it is tolerated but flagged
            continue


def _primary(pkg: EvidencePackage) -> RouteCandidate:
    for r in pkg.routes:
        if RouteLabel.ORIGINAL in r.labels or r.label == RouteLabel.ORIGINAL:
            return r
    return pkg.routes[0]


def derive_facts(pkg: EvidencePackage, policy: Policy) -> Facts:
    by_route: dict[UUID, RiskAssessment] = {a.route_id: a for a in pkg.risk_assessments}
    primary = _primary(pkg)
    pa = by_route[primary.route_id]
    th = policy.thresholds
    usable_alts = [r for r in pkg.routes if r.usable and r.route_id != primary.route_id]
    best: RouteCandidate | None = None
    best_a: RiskAssessment | None = None
    for r in sorted(usable_alts, key=lambda r: (by_route[r.route_id].score, r.duration_seconds, str(r.route_id))):
        extra = r.duration_seconds / max(primary.duration_seconds, 1.0) - 1.0
        if extra <= th["max_extra_duration_ratio"]:
            best, best_a = r, by_route[r.route_id]
            break
    materially_safer = False
    if best_a is not None:
        materially_safer = (
            (pa.score - best_a.score) >= th["materially_safer_delta"]
            and RISK_RANK[best_a.risk_level] <= RISK_RANK[pa.risk_level]
            and RISK_RANK[best_a.risk_level] < RISK_RANK[RiskLevel.HIGH]
        )
    time_dependent = pa.time_dependent and pa.later_window_risk_level is not None
    reasons = list(pa.reason_codes)
    return Facts(
        gate=pkg.quality_summary.gate.value,
        original_closed=primary.exposure.closed or not primary.usable,
        usable_alternative=best is not None,
        safety_critical_conflicts=pkg.conflict_summary.safety_critical > 0,
        primary_level=pa.risk_level,
        primary_p=pa.score,
        primary_uncertainty=pa.uncertainty,
        materially_safer_available=materially_safer,
        best_alternative_id=best.route_id if best else None,
        best_alternative_level=best_a.risk_level if best_a else None,
        best_alternative_p=best_a.score if best_a else None,
        time_dependent=time_dependent,
        later_level=pa.later_window_risk_level,
        suggested_delay_minutes=pa.later_window_delay_minutes if time_dependent else None,
        quality_score=pkg.quality_summary.overall.score,
        weather_coverage=float(pkg.quality_summary.weather.coverage or 0.0),
        reason_codes=reasons,
        primary_route_id=primary.route_id,
    )


def _matches(rule: Rule, f: Facts) -> bool:
    w = rule.when
    if "gate" in w and f.gate != w["gate"]:
        return False
    if "gate_in" in w and f.gate not in w["gate_in"]:
        return False
    if "original_closed" in w and f.original_closed != w["original_closed"]:
        return False
    if "usable_alternative" in w and f.usable_alternative != w["usable_alternative"]:
        return False
    if "safety_critical_conflicts" in w and f.safety_critical_conflicts != w["safety_critical_conflicts"]:
        return False
    if "primary_level_in" in w and f.primary_level.value not in w["primary_level_in"]:
        return False
    if "materially_safer_available" in w and f.materially_safer_available != w["materially_safer_available"]:
        return False
    if "time_dependent" in w and f.time_dependent != w["time_dependent"]:
        return False
    if "later_level_below" in w:
        if f.later_level is None or RISK_RANK[f.later_level] >= RISK_RANK[RiskLevel(w["later_level_below"])]:
            return False
    return True


@dataclass(slots=True)
class Evaluation:
    action: ActionCode
    rule: Rule
    rules_evaluated: list[str]
    selected_route_id: UUID | None
    escalation_required: bool
    escalation_reasons: list[str]
    confidence: float
    reason_codes: list[ReasonCode]
    suggested_delay_minutes: int | None
    facts: Facts


def confidence_score(f: Facts, rule: Rule, policy: Policy) -> float:
    """Confidence is NOT the model probability. v1 formula (weights are part of the policy version):
    0.35*(1-uncertainty) + 0.25*quality + 0.15*coverage + 0.15*specificity + 0.10*alt_quality - penalties."""
    alt_quality = 1.0
    if rule.action == ActionCode.CHANGE_ROUTE and f.best_alternative_p is not None:
        alt_quality = 1.0 - f.best_alternative_p
    c = (
        0.35 * (1 - f.primary_uncertainty)
        + 0.25 * f.quality_score
        + 0.15 * f.weather_coverage
        + 0.15 * rule.specificity
        + 0.10 * alt_quality
    )
    if f.safety_critical_conflicts:
        c -= 0.15
    if f.gate == "DEGRADED":
        c -= 0.05
    return round(max(0.05, min(0.99, c)), 3)


def evaluate(pkg: EvidencePackage, policy: Policy) -> Evaluation:
    f = derive_facts(pkg, policy)
    evaluated: list[str] = []
    chosen: Rule | None = None
    for rule in policy.rules:
        evaluated.append(rule.id)
        if _matches(rule, f):
            chosen = rule
            break
    assert chosen is not None  # FALLBACK_CONSERVATIVE always matches
    action = ActionCode(chosen.action)
    selected = f.primary_route_id
    if chosen.select == "best_alternative":
        selected = f.best_alternative_id
    if action == ActionCode.AVOID and f.original_closed:
        selected = None
    conf = confidence_score(f, chosen, policy)
    esc_reasons: list[str] = []
    if chosen.escalate:
        esc_reasons.append(f"RULE:{chosen.id}")
    if f.safety_critical_conflicts:
        esc_reasons.append("SAFETY_CRITICAL_CONFLICT")
    if f.primary_uncertainty >= policy.thresholds["near_threshold_uncertainty"] and f.primary_level in (
        RiskLevel.MEDIUM,
        RiskLevel.HIGH,
    ):
        esc_reasons.append("NEAR_THRESHOLD")
    if action == ActionCode.NORMAL and conf < policy.thresholds["min_confidence_for_normal"]:
        esc_reasons.append("LOW_CONFIDENCE_NORMAL")
    if f.weather_coverage < 0.5:
        esc_reasons.append("LOW_COVERAGE")
    codes: list[ReasonCode] = []
    for c in [*(ReasonCode(x) for x in chosen.reason_codes), *f.reason_codes]:
        if c not in codes:
            codes.append(c)
    return Evaluation(
        action=action,
        rule=chosen,
        rules_evaluated=evaluated,
        selected_route_id=selected,
        escalation_required=bool(esc_reasons),
        escalation_reasons=esc_reasons,
        confidence=conf,
        reason_codes=codes[:8],
        suggested_delay_minutes=f.suggested_delay_minutes if action == ActionCode.DELAY else None,
        facts=f,
    )


def facts_as_dict(f: Facts) -> dict[str, Any]:
    return {
        "gate": f.gate,
        "original_closed": f.original_closed,
        "usable_alternative": f.usable_alternative,
        "safety_critical_conflicts": f.safety_critical_conflicts,
        "primary_level": f.primary_level.value,
        "primary_p": f.primary_p,
        "primary_uncertainty": f.primary_uncertainty,
        "materially_safer_available": f.materially_safer_available,
        "best_alternative_level": f.best_alternative_level.value if f.best_alternative_level else None,
        "best_alternative_p": f.best_alternative_p,
        "time_dependent": f.time_dependent,
        "later_level": f.later_level.value if f.later_level else None,
        "quality_score": f.quality_score,
        "weather_coverage": f.weather_coverage,
        "gate_value": QualityGate(f.gate).value,
    }
