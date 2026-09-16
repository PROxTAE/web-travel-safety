"""Risk assessment = model probability (or rule baseline when unavailable) + deterministic safety overrides.

Invariants:
- overrides can only raise the level (monotonic safety)
- missing critical evidence never yields LOW (becomes UNKNOWN)
- outputs carry model/threshold/feature-schema versions and controlled reason codes
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sta_contracts.enums import SEVERITY_RANK, DataStatus, QualityFlag, ReasonCode, RiskLevel
from sta_contracts.models import (
    DataQuality,
    DisasterEvent,
    IntegratedTravelContext,
    ModelRef,
    RiskAssessment,
    RouteCandidate,
    SafetyOverride,
)

from app.risk.model_loader import LoadedModel
from app.risk.thresholds import RISK_RANK, Overrides, Thresholds, max_level

RULE_BASELINE_VERSION = "rule-1.0.0"
# feature -> reason code used to explain model contributions (top positive contributors only)
CONTRIB_REASON: dict[str, ReasonCode] = {
    "weather_max_precip_mm": ReasonCode.HEAVY_PRECIPITATION,
    "weather_max_precip_probability": ReasonCode.HEAVY_PRECIPITATION,
    "severe_weather_exposure_minutes": ReasonCode.SEVERE_WEATHER_CORRIDOR,
    "weather_max_severity_rank": ReasonCode.SEVERE_WEATHER_CORRIDOR,
    "weather_max_wind_gust_kmh": ReasonCode.HIGH_WIND,
    "weather_min_visibility_m": ReasonCode.LOW_VISIBILITY,
    "weather_max_temperature_c": ReasonCode.EXTREME_TEMPERATURE,
    "weather_min_temperature_c": ReasonCode.EXTREME_TEMPERATURE,
    "earthquake_max_magnitude_near_corridor": ReasonCode.EARTHQUAKE_NEAR_CORRIDOR,
    "active_disaster_event_count": ReasonCode.ACTIVE_DISASTER_EVENT,
    "max_disaster_severity_rank": ReasonCode.ACTIVE_DISASTER_EVENT,
    "active_official_alert_count": ReasonCode.OFFICIAL_ALERT_ACTIVE,
    "official_closure_count": ReasonCode.OFFICIAL_CLOSURE,
    "closed_segment_count": ReasonCode.OFFICIAL_CLOSURE,
    "delayed_segment_count": ReasonCode.TRANSPORT_DELAY,
    "max_delay_minutes": ReasonCode.TRANSPORT_DELAY,
    "stale_source_ratio": ReasonCode.STALE_DATA,
    "conflict_count": ReasonCode.CONFLICTING_SOURCES,
    "missing_critical_count": ReasonCode.INSUFFICIENT_EVIDENCE,
}


def rule_baseline_probability(f: dict[str, Any]) -> float:
    """Deterministic rule-only baseline (also the training comparison baseline). Version rule-1.0.0."""
    p = 0.05
    precip = f.get("weather_max_precip_mm")
    gust = f.get("weather_max_wind_gust_kmh")
    sev = f.get("weather_max_severity_rank")
    if precip is not None:
        p += min(0.5, float(precip) / 60)
    if gust is not None:
        p += min(0.4, max(0.0, (float(gust) - 40) / 120))
    if sev is not None and float(sev) >= 4:
        p += 0.25
    if (f.get("active_official_alert_count") or 0) >= 1:
        p += 0.3
    if (f.get("official_closure_count") or 0) >= 1:
        p = 1.0
    mag = f.get("earthquake_max_magnitude_near_corridor")
    if mag is not None:
        p += max(0.0, (float(mag) - 4.5) / 4)
    if (f.get("delayed_segment_count") or 0) >= 1:
        p += 0.1
    return round(max(0.0, min(1.0, p)), 4)


def _official_alert_rank(snapshot: IntegratedTravelContext, route: RouteCandidate) -> int:
    ids = set(route.exposure.hazard_event_ids)
    ranks = [SEVERITY_RANK[e.severity] for e in snapshot.official_alerts if e.event_id in ids]
    return max(ranks, default=0)


def assess_route(
    snapshot: IntegratedTravelContext,
    route: RouteCandidate,
    *,
    model: LoadedModel | None,
    thresholds: Thresholds,
    overrides: Overrides,
    feature_schema_version: str,
    now: datetime | None = None,
) -> RiskAssessment:
    now = now or datetime.now(UTC)
    # Route-specific feature view: snapshot features are for the primary route; per-route exposure overrides the
    # event-derived fields so alternatives are scored on their own corridor exposure.
    f: dict[str, Any] = dict(snapshot.features)
    f["official_closure_count"] = 1 if route.exposure.closed else 0
    f["closed_segment_count"] = 1 if route.exposure.closed else 0
    f["active_disaster_event_count"] = len(route.exposure.hazard_event_ids)
    f["max_disaster_severity_rank"] = SEVERITY_RANK[route.exposure.max_hazard_severity] or None
    f["official_alert_max_severity_rank"] = _official_alert_rank(snapshot, route)
    official_ids = {e.event_id for e in snapshot.official_alerts}
    f["active_official_alert_count"] = sum(1 for h in route.exposure.hazard_event_ids if h in official_ids)
    f["severe_weather_exposure_minutes"] = route.exposure.severe_weather_minutes
    if route.exposure.min_hazard_distance_km is not None:
        f["earthquake_min_distance_km"] = route.exposure.min_hazard_distance_km

    reason_codes: list[ReasonCode] = []
    quality_notes: list[str] = []
    if model is not None:
        p = model.predict_proba(f)
        contribs = model.contributions(f)
        model_ref = ModelRef(
            name=model.name,
            version=model.version,
            feature_schema_version=model.feature_schema_version,
            thresholds_version=thresholds.version,
            checksum=model.checksum,
        )
        top = sorted(((k, v) for k, v in contribs.items() if v > 0.15), key=lambda kv: -kv[1])[:4]
        for k, _ in top:
            code = CONTRIB_REASON.get(k)
            if code and code not in reason_codes:
                reason_codes.append(code)
    else:
        p = rule_baseline_probability(f)
        contribs = {}
        model_ref = ModelRef(
            name="rule-baseline",
            version=RULE_BASELINE_VERSION,
            feature_schema_version=feature_schema_version,
            thresholds_version=thresholds.version,
        )
        reason_codes.append(ReasonCode.MODEL_UNAVAILABLE)
        quality_notes.append("local model unavailable; deterministic rule baseline used")

    level = thresholds.level(p)
    applied: list[SafetyOverride] = []
    for rule in overrides.rules:
        if rule.fires(f):
            applied.append(
                SafetyOverride(
                    code=rule.reason_code,
                    minimum_risk=rule.minimum_risk,
                    event_ids=list(route.exposure.hazard_event_ids)
                    if "OFFICIAL" in rule.id or "QUAKE" in rule.id
                    else [],
                    policy_version=overrides.version,
                )
            )
            level = max_level(level, rule.minimum_risk)
            if rule.reason_code not in reason_codes:
                reason_codes.append(rule.reason_code)

    # score/level consistency: an override-enforced level floors the reported score at that level's threshold,
    # so downstream ranking (which uses score) can never prefer a route under an official restriction
    if applied:
        floor = {RiskLevel.HIGH: thresholds.high, RiskLevel.MEDIUM: thresholds.medium}.get(level, 0.0)
        p = max(p, floor)

    # evidence sufficiency: cannot claim LOW without critical evidence
    coverage = float(f.get("weather_coverage_ratio") or 0.0)
    missing = int(f.get("missing_critical_count") or 0)
    insufficient = coverage < overrides.weather_coverage_below or missing >= overrides.missing_critical_at_least
    if insufficient:
        if ReasonCode.LOW_DATA_COVERAGE not in reason_codes:
            reason_codes.append(ReasonCode.LOW_DATA_COVERAGE)
        if level == RiskLevel.LOW:
            level = overrides.result_when_low
    if not reason_codes and level == RiskLevel.LOW:
        reason_codes.append(ReasonCode.CONDITIONS_NORMAL)
    if snapshot.quality_summary.overall.status == DataStatus.STALE and ReasonCode.STALE_DATA not in reason_codes:
        reason_codes.append(ReasonCode.STALE_DATA)
    if snapshot.conflict_summary.safety_critical and ReasonCode.CONFLICTING_SOURCES not in reason_codes:
        reason_codes.append(ReasonCode.CONFLICTING_SOURCES)

    # time dependence via the DELAYED probe (same feature schema)
    time_dependent = False
    later_level = None
    later_delay = None
    if snapshot.features_delayed and snapshot.delay_probe_minutes and not route.exposure.closed:
        fd = dict(snapshot.features_delayed)
        fd.update(
            {k: f[k] for k in ("official_closure_count", "closed_segment_count", "official_alert_max_severity_rank")}
        )
        p_later = model.predict_proba(fd) if model is not None else rule_baseline_probability(fd)
        later_level = thresholds.level(p_later)
        for rule in overrides.rules:
            if rule.fires(fd):
                later_level = max_level(later_level, rule.minimum_risk)
        later_delay = snapshot.delay_probe_minutes
        if p - p_later >= thresholds.delay_improvement_min and RISK_RANK[later_level] < RISK_RANK[level]:
            time_dependent = True
            if ReasonCode.RISK_DECREASES_LATER not in reason_codes:
                reason_codes.append(ReasonCode.RISK_DECREASES_LATER)

    uncertainty = round(1 - abs(2 * p - 1), 3)
    if thresholds.near_threshold(p):
        uncertainty = max(uncertainty, 0.6)
    qscore = snapshot.quality_summary.overall.score
    quality = DataQuality(
        status=snapshot.quality_summary.overall.status,
        score=round(qscore * (0.8 if model is None else 1.0), 3),
        flags=sorted(
            {*snapshot.quality_summary.overall.flags, *([QualityFlag.INCOMPLETE] if insufficient else [])},
            key=lambda x: x.value,
        ),
        coverage=coverage,
        completeness=snapshot.quality_summary.overall.completeness,
        freshness_seconds=snapshot.quality_summary.overall.freshness_seconds,
        notes=quality_notes + (["near decision threshold"] if thresholds.near_threshold(p) else []),
    )
    return RiskAssessment(
        assessment_id=uuid4(),
        snapshot_id=snapshot.snapshot_id,
        route_id=route.route_id,
        score=round(p, 4),
        probability_high=round(p, 4),
        risk_level=level,
        uncertainty=uncertainty,
        reason_codes=reason_codes[:8],
        safety_overrides=applied,
        feature_contributions={k: v for k, v in contribs.items() if abs(v) > 0.05},
        time_dependent=time_dependent,
        later_window_risk_level=later_level,
        later_window_delay_minutes=later_delay,
        model=model_ref,
        quality=quality,
        created_at=now,
    )


def assess_all(snapshot: IntegratedTravelContext, route_ids: list[UUID] | None, **kw: Any) -> list[RiskAssessment]:
    routes = [r for r in snapshot.route_candidates if not route_ids or r.route_id in route_ids]
    return [assess_route(snapshot, r, **kw) for r in routes]


__all__ = ["assess_route", "assess_all", "rule_baseline_probability", "RULE_BASELINE_VERSION", "DisasterEvent"]
