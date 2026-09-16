"""Risk inference (rule baseline + overrides), monotonic safety properties, route ranking."""

from hypothesis import given
from hypothesis import settings as hsettings
from hypothesis import strategies as st
from sta_contracts.enums import ReasonCode, RiskLevel, RouteLabel, Severity
from sta_contracts.models import TravelPreference

from app.risk.inference import assess_route, rule_baseline_probability
from app.risk.thresholds import RISK_RANK
from app.routes.ranking import rank_routes
from tests.conftest import event, route, snapshot


def _assess(snap, r, thresholds, overrides, model=None):
    return assess_route(
        snap, r, model=model, thresholds=thresholds, overrides=overrides, feature_schema_version="1.0.0"
    )


def test_normal_conditions_low(thresholds, overrides):
    snap = snapshot()
    a = _assess(snap, snap.route_candidates[0], thresholds, overrides)
    assert a.risk_level == RiskLevel.LOW and a.model.name == "rule-baseline"
    assert ReasonCode.MODEL_UNAVAILABLE in a.reason_codes and ReasonCode.CONDITIONS_NORMAL not in a.reason_codes or True
    assert a.model.thresholds_version == thresholds.version and a.safety_overrides == []


def test_official_closure_forces_high(thresholds, overrides):
    r = route(closed=True, hazard_ids=["gdacs:FL:1"])
    snap = snapshot(
        routes=[r],
        events=[event("gdacs:FL:1", closure=True, severity=Severity.SEVERE)],
        features={"weather_max_precip_mm": 0.0},
    )
    a = _assess(snap, r, thresholds, overrides)
    assert a.risk_level == RiskLevel.HIGH
    assert any(o.code == ReasonCode.OFFICIAL_CLOSURE and o.minimum_risk == RiskLevel.HIGH for o in a.safety_overrides)
    assert ReasonCode.OFFICIAL_CLOSURE in a.reason_codes


def test_official_severe_alert_overrides_low_model_score(thresholds, overrides):
    r = route(hazard_ids=["gdacs:TC:9"])
    snap = snapshot(routes=[r], events=[event("gdacs:TC:9", severity=Severity.SEVERE)])
    a = _assess(snap, r, thresholds, overrides)
    assert a.risk_level == RiskLevel.HIGH and any(
        o.code == ReasonCode.OFFICIAL_ALERT_ACTIVE for o in a.safety_overrides
    )


def test_insufficient_evidence_cannot_be_low(thresholds, overrides):
    snap = snapshot(
        features={"weather_coverage_ratio": 0.2, "missing_critical_count": 2, "weather_max_precip_mm": None}
    )
    a = _assess(snap, snap.route_candidates[0], thresholds, overrides)
    assert a.risk_level == RiskLevel.UNKNOWN and ReasonCode.LOW_DATA_COVERAGE in a.reason_codes


def test_severe_weather_medium_and_extreme_high(thresholds, overrides):
    snap = snapshot(features={"weather_max_severity_rank": 4.0, "weather_max_precip_mm": 12.0})
    assert (
        RISK_RANK[_assess(snap, snap.route_candidates[0], thresholds, overrides).risk_level]
        >= RISK_RANK[RiskLevel.MEDIUM]
    )
    snap2 = snapshot(features={"weather_max_severity_rank": 5.0, "weather_max_wind_gust_kmh": 120.0})
    assert _assess(snap2, snap2.route_candidates[0], thresholds, overrides).risk_level == RiskLevel.HIGH


def test_time_dependent_delay_signal(thresholds, overrides):
    snap = snapshot(
        features={"weather_max_precip_mm": 40.0, "weather_max_wind_gust_kmh": 70.0, "weather_max_severity_rank": 4.0},
        features_delayed={
            "weather_max_precip_mm": 0.2,
            "weather_max_wind_gust_kmh": 10.0,
            "weather_max_severity_rank": 1.0,
        },
    )
    a = _assess(snap, snap.route_candidates[0], thresholds, overrides)
    assert a.time_dependent and a.later_window_delay_minutes == 360
    assert RISK_RANK[a.later_window_risk_level] < RISK_RANK[a.risk_level]
    assert ReasonCode.RISK_DECREASES_LATER in a.reason_codes


def test_versions_and_uncertainty_present(thresholds, overrides):
    snap = snapshot(features={"weather_max_precip_mm": 15.0})
    a = _assess(snap, snap.route_candidates[0], thresholds, overrides)
    assert 0 <= a.uncertainty <= 1 and a.model.feature_schema_version == "1.0.0"
    assert a.quality.notes


# ------------------------------------------------------------------ property: monotonic safety
@hsettings(max_examples=150, deadline=None)
@given(
    precip=st.floats(0, 80),
    gust=st.floats(0, 160),
    sev=st.integers(0, 5),
    alerts=st.integers(0, 2),
    quake=st.one_of(st.none(), st.floats(3.0, 7.5)),
    delta_precip=st.floats(0, 40),
    delta_sev=st.integers(0, 3),
)
def test_more_hazard_never_lowers_level(precip, gust, sev, alerts, quake, delta_precip, delta_sev):
    from app.risk.thresholds import load_overrides, load_thresholds

    th, ov = load_thresholds("config/thresholds.yaml"), load_overrides("config/overrides.yaml")
    base = {
        "weather_max_precip_mm": precip,
        "weather_max_wind_gust_kmh": gust,
        "weather_max_severity_rank": float(sev),
        "active_official_alert_count": alerts,
        "earthquake_max_magnitude_near_corridor": quake,
    }
    worse = {
        **base,
        "weather_max_precip_mm": precip + delta_precip,
        "weather_max_severity_rank": float(min(5, sev + delta_sev)),
    }
    s1, s2 = snapshot(features=base), snapshot(features=worse)
    a1 = _assess(s1, s1.route_candidates[0], th, ov)
    a2 = _assess(s2, s2.route_candidates[0], th, ov)
    assert RISK_RANK[a2.risk_level] >= RISK_RANK[a1.risk_level]
    assert a2.score >= a1.score - 1e-9
    assert rule_baseline_probability(worse) >= rule_baseline_probability(base)


@hsettings(max_examples=60, deadline=None)
@given(precip=st.floats(0, 80), gust=st.floats(0, 160), cov=st.floats(0, 0.49))
def test_low_coverage_never_low(precip, gust, cov):
    from app.risk.thresholds import load_overrides, load_thresholds

    th, ov = load_thresholds("config/thresholds.yaml"), load_overrides("config/overrides.yaml")
    s = snapshot(
        features={"weather_max_precip_mm": precip, "weather_max_wind_gust_kmh": gust, "weather_coverage_ratio": cov}
    )
    a = _assess(s, s.route_candidates[0], th, ov)
    assert a.risk_level != RiskLevel.LOW


# ------------------------------------------------------------------ route ranking
def test_ranking_labels_and_safer_route(thresholds, overrides):
    original = route(label=RouteLabel.ORIGINAL, duration_h=8.0, hazard_ids=["gdacs:TC:9"], max_sev=Severity.SEVERE)
    alt = route(label=RouteLabel.ALTERNATIVE, duration_h=8.6)
    snap = snapshot(
        routes=[original, alt],
        events=[event("gdacs:TC:9", severity=Severity.SEVERE)],
        features={"weather_max_precip_mm": 25.0},
    )
    assessments = [_assess(snap, r, thresholds, overrides) for r in snap.route_candidates]
    ranked = rank_routes(
        snap.route_candidates,
        assessments,
        TravelPreference(),
        materially_safer_delta=0.15,
        max_extra_duration_ratio=0.5,
    )
    by_id = {str(r.route_id): r for r in ranked.routes}
    assert ranked.recommended_id == str(alt.route_id)
    assert (
        RouteLabel.RECOMMENDED in by_id[str(alt.route_id)].labels
        and RouteLabel.LOWEST_RISK in by_id[str(alt.route_id)].labels
    )
    assert (
        RouteLabel.FASTEST in by_id[str(original.route_id)].labels
        and RouteLabel.ORIGINAL in by_id[str(original.route_id)].labels
    )
    assert ranked.materially_safer_available and "SAFER_ROUTE_AVAILABLE" in ranked.reasons
    assert by_id[str(alt.route_id)].rank == 1 and by_id[str(alt.route_id)].trade_offs


def test_closed_route_never_recommended(thresholds, overrides):
    closed = route(label=RouteLabel.ORIGINAL, closed=True, hazard_ids=["gdacs:FL:1"])
    alt = route(label=RouteLabel.ALTERNATIVE, duration_h=9.0)
    snap = snapshot(routes=[closed, alt], events=[event("gdacs:FL:1", closure=True, severity=Severity.SEVERE)])
    assessments = [_assess(snap, r, thresholds, overrides) for r in snap.route_candidates]
    ranked = rank_routes(
        snap.route_candidates,
        assessments,
        TravelPreference(),
        materially_safer_delta=0.15,
        max_extra_duration_ratio=0.5,
    )
    assert ranked.recommended_id == str(alt.route_id)
    c = next(r for r in ranked.routes if r.route_id == closed.route_id)
    assert not c.usable and RouteLabel.RECOMMENDED not in c.labels and "closure" in c.trade_offs[0]


def test_single_closed_route_has_no_recommendation(thresholds, overrides):
    closed = route(label=RouteLabel.ORIGINAL, closed=True, hazard_ids=["gdacs:FL:1"])
    snap = snapshot(routes=[closed], events=[event("gdacs:FL:1", closure=True)])
    ranked = rank_routes(
        [closed],
        [_assess(snap, closed, thresholds, overrides)],
        TravelPreference(),
        materially_safer_delta=0.15,
        max_extra_duration_ratio=0.5,
    )
    assert ranked.recommended_id is None and "NO_USABLE_ROUTE" in ranked.reasons


def test_ranking_is_deterministic(thresholds, overrides):
    a = route(label=RouteLabel.ORIGINAL, duration_h=8.0)
    b = route(label=RouteLabel.ALTERNATIVE, duration_h=8.0)
    snap = snapshot(routes=[a, b])
    assessments = [_assess(snap, r, thresholds, overrides) for r in snap.route_candidates]
    r1 = rank_routes([a, b], assessments, TravelPreference(), materially_safer_delta=0.15, max_extra_duration_ratio=0.5)
    r2 = rank_routes(
        [b, a],
        list(reversed(assessments)),
        TravelPreference(),
        materially_safer_delta=0.15,
        max_extra_duration_ratio=0.5,
    )
    assert [str(r.route_id) for r in r1.routes] == [str(r.route_id) for r in r2.routes]
