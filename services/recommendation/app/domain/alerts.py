"""Meaningful-change rules v1.0.0, event hash dedup, per-severity cooldown, escalation never suppressed."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sta_contracts.enums import SEVERITY_RANK, ActionCode, RiskLevel, Severity
from sta_contracts.models import RecommendationResponse

RULES_VERSION = "1.0.0"
ACTION_SEVERITY = {ActionCode.NORMAL: 0, ActionCode.DELAY: 1, ActionCode.CHANGE_ROUTE: 1, ActionCode.AVOID: 2}
RISK_RANK = {RiskLevel.UNKNOWN: 0, RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3}
COOLDOWN_MINUTES = {
    Severity.INFO: 240,
    Severity.MINOR: 120,
    Severity.MODERATE: 60,
    Severity.SEVERE: 30,
    Severity.EXTREME: 0,
    Severity.UNKNOWN: 120,
}


@dataclass(slots=True)
class ChangeAssessment:
    meaningful: bool
    severity: Severity
    escalation: bool
    reasons: list[str] = field(default_factory=list)
    event_hash: str = ""
    title_key: str = ""


def _alert_ids(r: RecommendationResponse) -> set[str]:
    return {a.alert_id for a in r.alerts}


def assess_change(previous: RecommendationResponse | None, new: RecommendationResponse) -> ChangeAssessment:
    reasons: list[str] = []
    severity = Severity.INFO
    escalation = False
    if previous is None:
        # first recommendation for a subscription: notify only if not NORMAL
        if new.action_code != ActionCode.NORMAL:
            reasons.append(f"INITIAL_{new.action_code.value}")
            severity = Severity.MODERATE if new.action_code != ActionCode.AVOID else Severity.SEVERE
    else:
        if ACTION_SEVERITY[new.action_code] > ACTION_SEVERITY[previous.action_code]:
            reasons.append(f"ACTION_ESCALATED:{previous.action_code.value}->{new.action_code.value}")
            severity = Severity.SEVERE if new.action_code == ActionCode.AVOID else Severity.MODERATE
            escalation = True
        if RISK_RANK[new.risk_level] > RISK_RANK[previous.risk_level]:
            reasons.append(f"RISK_INCREASED:{previous.risk_level.value}->{new.risk_level.value}")
            severity = max(
                severity,
                Severity.MODERATE if new.risk_level == RiskLevel.MEDIUM else Severity.SEVERE,
                key=lambda s: SEVERITY_RANK[s],
            )
            escalation = True
        new_alerts = _alert_ids(new) - _alert_ids(previous)
        closures = [a for a in new.alerts if a.alert_id in new_alerts and a.official]
        if closures:
            reasons.append("NEW_OFFICIAL_ALERT:" + ",".join(sorted(a.alert_id for a in closures)[:3]))
            top = max((a.severity for a in closures), key=lambda s: SEVERITY_RANK[s])
            severity = max(severity, top, key=lambda s: SEVERITY_RANK[s])
            escalation = escalation or SEVERITY_RANK[top] >= SEVERITY_RANK[Severity.SEVERE]
        if previous.action_code in (ActionCode.AVOID, ActionCode.DELAY) and new.action_code in (
            ActionCode.NORMAL,
            ActionCode.CHANGE_ROUTE,
        ):
            reasons.append(f"MATERIALLY_SAFER_OPTION:{previous.action_code.value}->{new.action_code.value}")
            severity = max(severity, Severity.MINOR, key=lambda s: SEVERITY_RANK[s])
        if (
            previous.status == "PARTIAL"
            and new.status == "COMPLETED"
            and RISK_RANK[new.risk_level] >= RISK_RANK[RiskLevel.MEDIUM]
        ):
            reasons.append("CRITICAL_FRESHNESS_RECOVERED")
            severity = max(severity, Severity.MINOR, key=lambda s: SEVERITY_RANK[s])
    meaningful = bool(reasons)
    h = hashlib.sha256(
        "|".join(
            [
                str(new.trip_id),
                new.action_code.value,
                new.risk_level.value,
                ",".join(sorted(_alert_ids(new))),
                str(new.versions.policy),
                RULES_VERSION,
            ]
        ).encode()
    ).hexdigest()
    return ChangeAssessment(
        meaningful=meaningful,
        severity=severity,
        escalation=escalation,
        reasons=reasons,
        event_hash=h,
        title_key=reasons[0].split(":")[0] if reasons else "",
    )


def cooldown_allows(
    severity: Severity, escalation: bool, cooldown_until: datetime | None, now: datetime | None = None
) -> bool:
    """Cooldown suppresses repeats of equal/lower severity; escalation to higher severity is never suppressed."""
    now = now or datetime.now(UTC)
    if escalation or severity == Severity.EXTREME:
        return True
    return cooldown_until is None or cooldown_until <= now


def next_cooldown(severity: Severity, now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now + timedelta(minutes=COOLDOWN_MINUTES[severity])
