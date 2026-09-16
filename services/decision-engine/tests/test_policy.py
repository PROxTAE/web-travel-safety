"""Golden scenarios, threshold boundaries, consistency gate, Hypothesis monotonic safety."""

from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import settings as hsettings
from hypothesis import strategies as st
from sta_contracts.enums import ActionCode, QualityGate, RiskLevel, RouteLabel

from app.policy.evaluator import ACTION_RANK, ConsistencyError, check_consistency, evaluate
from tests.conftest import assessment, official_alert, package, route, travel_request

SNAP = uuid4()


def _pkg(
    req,
    primary_level,
    p,
    *,
    alt=None,
    alt_level=None,
    alt_p=None,
    closed=False,
    gate=QualityGate.PASS,
    conflicts=0,
    time_dependent=False,
    later=None,
    uncertainty=0.2,
    alt_duration=8.5,
):
    orig = route(RouteLabel.ORIGINAL, closed=closed, hazard_ids=["gdacs:FL:1"] if closed else ())
    routes = [orig]
    assessments = [
        assessment(
            orig,
            SNAP,
            p,
            primary_level,
            uncertainty=uncertainty,
            time_dependent=time_dependent,
            later=later,
            delay=360 if time_dependent else None,
        )
    ]
    if alt is not None:
        a = route(
            RouteLabel.ALTERNATIVE, duration_h=alt_duration, labels=[RouteLabel.ALTERNATIVE, RouteLabel.RECOMMENDED]
        )
        routes.append(a)
        assessments.append(assessment(a, SNAP, alt_p, alt_level))
    return package(
        req,
        routes=routes,
        assessments=assessments,
        gate=gate,
        conflicts=conflicts,
        alerts=[official_alert(closure=closed)] if closed else [],
    )


GOLDEN = [
    ("safe", dict(primary_level=RiskLevel.LOW, p=0.05), ActionCode.NORMAL, "LOW_NORMAL", False),
    (
        "medium_no_option",
        dict(primary_level=RiskLevel.MEDIUM, p=0.35),
        ActionCode.NORMAL,
        "MEDIUM_PROCEED_WITH_CAUTION",
        False,
    ),
    (
        "medium_safer_route",
        dict(primary_level=RiskLevel.MEDIUM, p=0.4, alt=True, alt_level=RiskLevel.LOW, alt_p=0.1),
        ActionCode.CHANGE_ROUTE,
        "MEDIUM_WITH_SAFER_ROUTE",
        False,
    ),
    (
        "high_safer_route",
        dict(primary_level=RiskLevel.HIGH, p=0.8, alt=True, alt_level=RiskLevel.LOW, alt_p=0.2),
        ActionCode.CHANGE_ROUTE,
        "HIGH_WITH_SAFER_ROUTE",
        False,
    ),
    (
        "high_alt_too_slow",
        dict(primary_level=RiskLevel.HIGH, p=0.8, alt=True, alt_level=RiskLevel.LOW, alt_p=0.2, alt_duration=13.0),
        ActionCode.AVOID,
        "HIGH_NO_OPTION",
        False,
    ),
    (
        "high_time_dependent",
        dict(primary_level=RiskLevel.HIGH, p=0.8, time_dependent=True, later=RiskLevel.LOW),
        ActionCode.DELAY,
        "HIGH_TIME_DEPENDENT",
        False,
    ),
    ("high_no_option", dict(primary_level=RiskLevel.HIGH, p=0.8), ActionCode.AVOID, "HIGH_NO_OPTION", False),
    (
        "closure_no_alt",
        dict(primary_level=RiskLevel.HIGH, p=1.0, closed=True),
        ActionCode.AVOID,
        "OFFICIAL_CLOSURE_NO_ALTERNATIVE",
        False,
    ),
    (
        "closure_with_alt",
        dict(primary_level=RiskLevel.HIGH, p=1.0, closed=True, alt=True, alt_level=RiskLevel.LOW, alt_p=0.1),
        ActionCode.CHANGE_ROUTE,
        "OFFICIAL_CLOSURE_WITH_ALTERNATIVE",
        False,
    ),
    (
        "closure_low_model_score",
        dict(primary_level=RiskLevel.LOW, p=0.05, closed=True),
        ActionCode.AVOID,
        "OFFICIAL_CLOSURE_NO_ALTERNATIVE",
        False,
    ),
    (
        "block_gate",
        dict(primary_level=RiskLevel.LOW, p=0.05, gate=QualityGate.BLOCK),
        ActionCode.AVOID,
        "BLOCKED_QUALITY_GATE",
        True,
    ),
    ("unknown_evidence", dict(primary_level=RiskLevel.UNKNOWN, p=0.1), ActionCode.DELAY, "UNKNOWN_EVIDENCE", True),
    (
        "conflicting_high",
        dict(primary_level=RiskLevel.HIGH, p=0.7, conflicts=1, alt=True, alt_level=RiskLevel.LOW, alt_p=0.1),
        ActionCode.AVOID,
        "CONFLICTING_OFFICIAL_SOURCES_HIGH",
        True,
    ),
    (
        "medium_time_dependent",
        dict(primary_level=RiskLevel.MEDIUM, p=0.4, time_dependent=True, later=RiskLevel.LOW),
        ActionCode.DELAY,
        "MEDIUM_TIME_DEPENDENT",
        False,
    ),
]


@pytest.mark.parametrize("name,kw,action,rule,escalate", GOLDEN, ids=[g[0] for g in GOLDEN])
def test_golden(policy, name, kw, action, rule, escalate):
    req = travel_request()
    pkg = _pkg(req, **kw)
    ev = evaluate(pkg, policy)
    assert ev.action == action, f"{name}: got {ev.action} via {ev.rule.id}"
    assert ev.rule.id == rule
    assert ev.escalation_required == escalate or (escalate and ev.escalation_required)
    assert 0.05 <= ev.confidence <= 0.99
    if action == ActionCode.CHANGE_ROUTE:
        assert ev.selected_route_id is not None and ev.selected_route_id != pkg.routes[0].route_id
    if action == ActionCode.AVOID and kw.get("closed"):
        assert ev.selected_route_id is None
    if action == ActionCode.DELAY and kw.get("time_dependent"):
        assert ev.suggested_delay_minutes == 360


def test_materially_safer_boundary(policy):
    req = travel_request()
    delta = policy.thresholds["materially_safer_delta"]
    # exactly at delta -> safer; just below -> not safer
    at = _pkg(req, RiskLevel.MEDIUM, 0.40, alt=True, alt_level=RiskLevel.LOW, alt_p=round(0.40 - delta, 4))
    below = _pkg(req, RiskLevel.MEDIUM, 0.40, alt=True, alt_level=RiskLevel.LOW, alt_p=round(0.40 - delta + 0.01, 4))
    assert evaluate(at, policy).action == ActionCode.CHANGE_ROUTE
    assert evaluate(below, policy).action == ActionCode.NORMAL


def test_extra_duration_boundary(policy):
    req = travel_request()
    ratio = policy.thresholds["max_extra_duration_ratio"]
    ok = _pkg(req, RiskLevel.HIGH, 0.8, alt=True, alt_level=RiskLevel.LOW, alt_p=0.1, alt_duration=8.0 * (1 + ratio))
    too_long = _pkg(
        req, RiskLevel.HIGH, 0.8, alt=True, alt_level=RiskLevel.LOW, alt_p=0.1, alt_duration=8.0 * (1 + ratio) + 0.1
    )
    assert evaluate(ok, policy).action == ActionCode.CHANGE_ROUTE
    assert evaluate(too_long, policy).action == ActionCode.AVOID


def test_consistency_gate(policy):
    req = travel_request()
    pkg = _pkg(req, RiskLevel.LOW, 0.05)
    other = travel_request()
    with pytest.raises(ConsistencyError, match="different request"):
        check_consistency(other, pkg, feature_schema_versions={"1.0.0"})
    with pytest.raises(ConsistencyError, match="revision"):
        check_consistency(
            travel_request(revision=2).model_copy(update={"request_id": req.request_id}),
            pkg,
            feature_schema_versions={"1.0.0"},
        )
    with pytest.raises(ConsistencyError, match="unsupported"):
        check_consistency(req, pkg, feature_schema_versions={"9.9.9"})
    bad = pkg.model_copy(update={"risk_assessments": pkg.risk_assessments[:0]})
    with pytest.raises(ConsistencyError, match="every route"):
        check_consistency(req, bad, feature_schema_versions={"1.0.0"})
    from datetime import timedelta

    from tests.conftest import NOW, evidence

    expired = pkg.model_copy(update={"evidence": [evidence(expires=NOW - timedelta(days=1))]})
    with pytest.raises(ConsistencyError, match="expired"):
        check_consistency(req, expired, feature_schema_versions={"1.0.0"})


LEVELS = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH]
FLOOR = {RiskLevel.LOW: 0.0, RiskLevel.MEDIUM: 0.25, RiskLevel.HIGH: 0.55}
CEIL = {RiskLevel.LOW: 0.2499, RiskLevel.MEDIUM: 0.5499, RiskLevel.HIGH: 1.0}


def consistent_p(level: RiskLevel, p: float) -> float:
    """Upstream (M06) floors the score at the level threshold; generated inputs must respect that invariant."""
    return round(min(CEIL[level], max(FLOOR[level], p)), 4)


@hsettings(max_examples=200, deadline=None)
@given(
    lvl=st.integers(0, 2),
    p=st.floats(0.0, 1.0),
    bump=st.integers(0, 2),
    closed=st.booleans(),
    add_closure=st.booleans(),
    alt=st.booleans(),
    alt_p=st.floats(0.0, 0.2499),
)
def test_monotonic_safety(lvl, p, bump, closed, add_closure, alt, alt_p):
    """Raising risk or adding an official restriction never yields a weaker action; NORMAL is impossible under a
    closure; lower data quality never raises confidence. (Closure + open alternative => CHANGE_ROUTE, which ranks
    with DELAY above NORMAL.)"""
    from app.policy.loader import load_policy

    policy = load_policy("policies/v1/decision-table.yaml", "schemas/decision-policy.schema.json")
    req = travel_request()
    p0 = consistent_p(LEVELS[lvl], p)
    kw = dict(alt=alt or None, alt_level=RiskLevel.LOW if alt else None, alt_p=alt_p if alt else None)
    base = _pkg(req, LEVELS[lvl], p0, closed=closed, **kw)
    worse_lvl = LEVELS[min(2, lvl + bump)]
    worse = _pkg(req, worse_lvl, consistent_p(worse_lvl, p0 + 0.1 * bump), closed=closed or add_closure, **kw)
    a1, a2 = evaluate(base, policy), evaluate(worse, policy)
    assert ACTION_RANK[a2.action] >= ACTION_RANK[a1.action]
    if closed or add_closure:
        assert a2.action != ActionCode.NORMAL
    degraded = worse.model_copy(
        update={"quality_summary": worse.quality_summary.model_copy(update={"gate": QualityGate.DEGRADED})}
    )
    assert evaluate(degraded, policy).confidence <= a2.confidence + 1e-9
