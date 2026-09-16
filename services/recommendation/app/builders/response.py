"""RecommendationResponse builder (08 plan §Recommendation builder rules).

Order: action → why → what to do (routes) → emergency → freshness/degraded/limitations → sources/versions.
The builder copies structured fields; it never reinterprets risk or policy. Invalid combinations are rejected.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sta_contracts.enums import SEVERITY_RANK, ActionCode, DataStatus, RiskLevel, RouteLabel, RunStatus
from sta_contracts.models import (
    AlertItem,
    DecisionResult,
    EmergencyInstruction,
    EvidencePackage,
    Freshness,
    OfficialContact,
    RecommendationCreateRequest,
    RecommendationResponse,
    RouteCandidate,
    SourceProvenance,
)

BUILDER_VERSION = "1.0.0"
POLICY_TTL_SECONDS = 60 * 60  # recommendation validity cap (min with evidence expiry)


class BuildError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def _validate(decision: DecisionResult, pkg: EvidencePackage) -> None:
    if decision.snapshot_id != pkg.snapshot_id or decision.request_id != pkg.request_id:
        raise BuildError("PACKAGE_MISMATCH", "decision and evidence package refer to different request/snapshot")
    if not decision.validation.locked_action:
        raise BuildError("ACTION_NOT_LOCKED", "decision action is not locked")
    routes = {r.route_id: r for r in pkg.routes}
    if decision.action_code == ActionCode.CHANGE_ROUTE:
        sel = routes.get(decision.selected_route_id) if decision.selected_route_id else None
        if sel is None or not sel.usable:
            raise BuildError("CHANGE_ROUTE_WITHOUT_USABLE_ROUTE", "CHANGE_ROUTE requires a usable selected route")
    if decision.action_code == ActionCode.AVOID and decision.selected_route_id:
        sel = routes.get(decision.selected_route_id)
        if sel is not None and sel.exposure.closed:
            raise BuildError("AVOID_WITH_CLOSED_PRIMARY", "AVOID must not present a closed route as primary")
    allowed = {e.evidence_id for e in pkg.evidence}
    if any(c not in allowed for c in decision.citations):
        raise BuildError("UNKNOWN_CITATION", "decision cites evidence not in the package")


def _primary_and_alternatives(
    decision: DecisionResult, pkg: EvidencePackage
) -> tuple[RouteCandidate | None, list[RouteCandidate]]:
    routes = list(pkg.routes)
    if decision.action_code == ActionCode.AVOID:
        usable = [r for r in routes if r.usable]
        return None, sorted(usable, key=lambda r: r.rank or 99)
    primary: RouteCandidate | None = None
    if decision.selected_route_id:
        primary = next((r for r in routes if r.route_id == decision.selected_route_id), None)
    if primary is None:
        primary = next(
            (r for r in routes if RouteLabel.ORIGINAL in r.labels or r.label == RouteLabel.ORIGINAL),
            routes[0] if routes else None,
        )
    alts = [r for r in routes if primary is None or r.route_id != primary.route_id]
    return primary, sorted(alts, key=lambda r: (not r.usable, r.rank or 99))


def _alerts(pkg: EvidencePackage) -> list[AlertItem]:
    seen: dict[str, AlertItem] = {}
    hazard_ids = {h for r in pkg.routes for h in r.exposure.hazard_event_ids}
    for e in pkg.official_alerts:
        if e.event_id in seen:
            continue
        seen[e.event_id] = AlertItem(
            alert_id=e.event_id,
            title=e.title,
            severity=e.severity,
            event_type=e.event_type,
            official=e.official,
            area=", ".join(e.country_codes) or None,
            effective_at=e.effective_at,
            ends_at=e.ends_at,
            source_url=e.source.source_url,
            observed_at=e.source.observed_at,
            fetched_at=e.source.fetched_at,
        )
    # non-official corridor hazards are still listed, after official ones, highest severity first
    items = sorted(seen.values(), key=lambda a: -SEVERITY_RANK[a.severity])
    return [a for a in items if a.official] + [a for a in items if not a.official and a.alert_id in hazard_ids]


def _sources(pkg: EvidencePackage, snapshot_sources: list[SourceProvenance]) -> list[SourceProvenance]:
    out: dict[UUID, SourceProvenance] = {}
    for s in snapshot_sources:
        out.setdefault(s.source_id, s)
    for r in pkg.routes:
        for s in r.sources:
            out.setdefault(s.source_id, s)
    for e in pkg.official_alerts:
        out.setdefault(e.source.source_id, e.source)
    return sorted(out.values(), key=lambda s: (s.provider, str(s.source_id)))


def _freshness(sources: list[SourceProvenance], snapshot_created_at: datetime) -> Freshness:
    observed = [s.observed_at for s in sources if s.observed_at]
    expires = [s.expires_at for s in sources if s.expires_at]
    return Freshness(
        observed_at=min(observed) if observed else None,
        fetched_at=snapshot_created_at,
        expires_at=min(expires) if expires else None,
    )


def _emergency_instructions(decision: DecisionResult, pkg: EvidencePackage) -> list[EmergencyInstruction]:
    """Approved structured steps: derived from cited official passages (bullet lines) — never free LLM text."""
    steps: list[EmergencyInstruction] = []
    if decision.risk_level not in (RiskLevel.HIGH, RiskLevel.MEDIUM) and decision.action_code == ActionCode.NORMAL:
        return steps
    cited = {c for c in decision.citations}
    for e in pkg.evidence:
        if cited and e.evidence_id not in cited:
            continue
        for line in e.passage.split("\n"):
            line = line.strip()
            if line.startswith("• ") and 12 <= len(line) <= 240:
                steps.append(EmergencyInstruction(step=len(steps) + 1, text=line[2:], citation_id=e.evidence_id))
            if len(steps) >= 6:
                return steps
    return steps


def build_response(
    req: RecommendationCreateRequest,
    *,
    contacts: list[OfficialContact],
    contact_limitations: list[str],
    now: datetime | None = None,
) -> RecommendationResponse:
    now = now or datetime.now(UTC)
    decision, pkg = req.decision, req.evidence_package
    _validate(decision, pkg)
    primary, alternatives = _primary_and_alternatives(decision, pkg)
    sources = _sources(pkg, req.snapshot_sources)
    freshness = _freshness(sources, req.snapshot_created_at)
    degraded = sorted(set(pkg.degraded_services))
    if decision.validation.used_fallback:
        degraded.append(f"decision-engine: explanation fallback ({decision.validation.fallback_reason})")
    limitations = list(dict.fromkeys([*decision.limitations, *pkg.limitations, *contact_limitations]))
    status = (
        RunStatus.PARTIAL
        if (
            degraded
            or pkg.quality_summary.overall.status in (DataStatus.PARTIAL, DataStatus.STALE, DataStatus.CONFLICTING)
        )
        else RunStatus.COMPLETED
    )
    expiry_candidates = [now + timedelta(seconds=POLICY_TTL_SECONDS)]
    if freshness.expires_at:
        expiry_candidates.append(freshness.expires_at)
    reasons = list(dict.fromkeys(decision.reasons))
    return RecommendationResponse(
        recommendation_id=uuid4(),
        request_id=req.travel_request.request_id,
        trip_id=req.travel_request.trip_id,
        conversation_id=req.travel_request.conversation_id,
        decision_id=decision.decision_id,
        snapshot_id=pkg.snapshot_id,
        status=status,
        action_code=decision.action_code,
        risk_level=decision.risk_level,
        confidence=decision.confidence,
        short_summary=decision.summary,
        immediate_actions=list(dict.fromkeys(decision.immediate_actions)),
        reasons=reasons,
        reason_codes=decision.reason_codes,
        primary_route=primary,
        alternatives=alternatives,
        alerts=_alerts(pkg),
        emergency_instructions=_emergency_instructions(decision, pkg),
        official_contacts=contacts,
        sources=sources,
        evidence=[e for e in pkg.evidence if e.evidence_id in set(decision.citations)] or pkg.evidence[:3],
        weather_summary=req.weather_summary,
        transport_summary=req.transport_summary,
        freshness=freshness,
        limitations=limitations[:12],
        degraded_services=degraded,
        escalation_required=decision.escalation_required,
        versions=decision.versions.model_copy(update={"contract": decision.versions.contract}),
        locale=decision.locale,
        expires_at=min(expiry_candidates),
        supersedes_recommendation_id=req.previous_recommendation_id,
        created_at=now,
    )
