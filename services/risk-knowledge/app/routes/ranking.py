"""Route evaluation (Part C): hard constraints, versioned cost, honest labels, deterministic ties, trade-off text.

Cost v1.0.0 = 0.70 * risk_score + 0.20 * extra_duration_ratio + 0.10 * exposure_score
             (+ 0.05 per transfer beyond the minimum, + preference adjustments)
Closed routes (official closure) are never usable and never RECOMMENDED. No geometry is ever fabricated:
if the provider gave one route, the output has one route.
"""

from __future__ import annotations

from dataclasses import dataclass

from sta_contracts.enums import RiskLevel, RouteLabel
from sta_contracts.models import RiskAssessment, RouteCandidate, TravelPreference

from app.risk.thresholds import RISK_RANK

RANKING_VERSION = "1.0.0"
W_RISK, W_TIME, W_EXPOSURE, W_TRANSFER = 0.70, 0.20, 0.10, 0.05


@dataclass(slots=True)
class RankedRoutes:
    routes: list[RouteCandidate]
    recommended_id: str | None
    materially_safer_available: bool
    reasons: list[str]


def _cost(
    route: RouteCandidate, a: RiskAssessment, min_duration: float, min_transfers: int, pref: TravelPreference
) -> float:
    extra = max(0.0, route.duration_seconds / max(min_duration, 1.0) - 1.0)
    cost = W_RISK * a.score + W_TIME * extra + W_EXPOSURE * route.exposure.score
    cost += W_TRANSFER * max(0, route.transfers - min_transfers)
    if pref.prefer_safer_route:
        cost += 0.1 * a.score
    if pref.max_extra_duration_minutes and extra * min_duration / 60 > pref.max_extra_duration_minutes:
        cost += 0.25  # beyond the traveller's tolerated extra time
    return round(cost, 6)


def rank_routes(
    routes: list[RouteCandidate],
    assessments: list[RiskAssessment],
    pref: TravelPreference,
    *,
    materially_safer_delta: float,
    max_extra_duration_ratio: float,
) -> RankedRoutes:
    by_id = {a.route_id: a for a in assessments}
    reasons: list[str] = []
    out: list[RouteCandidate] = []
    for r in routes:
        a = by_id.get(r.route_id)
        if a is None:
            continue
        r.risk_level = a.risk_level
        r.risk_score = a.score
        r.usable = not r.exposure.closed
        r.labels = [RouteLabel.ORIGINAL] if r.label == RouteLabel.ORIGINAL else []
        out.append(r)
    usable = [r for r in out if r.usable]
    if not usable:
        reasons.append("NO_USABLE_ROUTE")
        for i, r in enumerate(out):
            r.rank = i + 1
            r.trade_offs = ["Route intersects an official closure; not usable."]
        return RankedRoutes(routes=out, recommended_id=None, materially_safer_available=False, reasons=reasons)

    min_duration = min(r.duration_seconds for r in usable)
    min_transfers = min(r.transfers for r in usable)
    costs = {str(r.route_id): _cost(r, by_id[r.route_id], min_duration, min_transfers, pref) for r in usable}
    # deterministic ordering: cost, then risk rank, then duration, then route_id
    usable.sort(key=lambda r: (costs[str(r.route_id)], RISK_RANK[r.risk_level], r.duration_seconds, str(r.route_id)))
    for i, r in enumerate(usable):
        r.rank = i + 1
    recommended = usable[0]
    fastest = min(usable, key=lambda r: (r.duration_seconds, str(r.route_id)))
    lowest_risk = min(usable, key=lambda r: (by_id[r.route_id].score, r.duration_seconds, str(r.route_id)))
    recommended.labels.append(RouteLabel.RECOMMENDED)
    if RouteLabel.FASTEST not in fastest.labels:
        fastest.labels.append(RouteLabel.FASTEST)
    if RouteLabel.LOWEST_RISK not in lowest_risk.labels:
        lowest_risk.labels.append(RouteLabel.LOWEST_RISK)
    for r in usable:
        if r.label != RouteLabel.ORIGINAL:
            r.label = r.labels[0] if r.labels else RouteLabel.ALTERNATIVE
    for r in out:
        if not r.usable:
            r.rank = len(usable) + 1
            r.trade_offs = ["Route intersects an official closure; not usable."]

    original = next((r for r in out if RouteLabel.ORIGINAL in r.labels), None)
    safer = False
    if original is not None and original.usable:
        o = by_id[original.route_id]
        for r in usable:
            if r.route_id == original.route_id:
                continue
            a = by_id[r.route_id]
            extra_ratio = r.duration_seconds / max(original.duration_seconds, 1.0) - 1.0
            if (
                (o.score - a.score) >= materially_safer_delta
                and RISK_RANK[a.risk_level] <= RISK_RANK[o.risk_level]
                and extra_ratio <= max_extra_duration_ratio
            ):
                safer = True
                break
    elif original is not None and not original.usable and usable:
        safer = True  # original closed, an open alternative exists
    if safer:
        reasons.append("SAFER_ROUTE_AVAILABLE")

    # trade-off text (no LLM): deterministic templates with numbers
    for r in usable:
        a = by_id[r.route_id]
        extra_min = (r.duration_seconds - min_duration) / 60
        parts = [f"Risk {a.risk_level.value} (p={a.score:.2f})"]
        parts.append(
            f"{r.duration_seconds / 3600:.1f} h, {r.distance_m / 1000:.0f} km"
            + (f", +{extra_min:.0f} min vs fastest" if extra_min >= 1 else ", fastest")
        )
        if r.transfers:
            parts.append(f"{r.transfers} transfer(s)")
        if r.exposure.hazard_event_ids:
            parts.append(f"{len(r.exposure.hazard_event_ids)} hazard event(s) on corridor")
        if r.exposure.severe_weather_minutes:
            parts.append(f"{r.exposure.severe_weather_minutes:.0f} min severe weather exposure")
        if a.risk_level == RiskLevel.UNKNOWN:
            parts.append("insufficient evidence for a LOW rating")
        r.trade_offs = parts
    return RankedRoutes(
        routes=sorted(out, key=lambda r: r.rank or 999),
        recommended_id=str(recommended.route_id),
        materially_safer_available=safer,
        reasons=reasons,
    )
