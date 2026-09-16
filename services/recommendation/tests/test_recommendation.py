"""Builder, directory, feedback, alert rules, API."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sta_contracts.enums import ActionCode, DataStatus, RiskLevel, RouteLabel, RunStatus, Severity

from app.builders.response import BuildError, build_response
from app.domain.alerts import assess_change, cooldown_allows
from app.domain.feedback import prepare
from app.main import build
from app.settings import Settings
from tests.conftest import NOW, alert, create_request, decision, evidence, package, route, travel_request


# ------------------------------------------------------------------ builder
def test_builder_change_route_orders_routes_and_dedups(directory):
    req = travel_request()
    orig = route(RouteLabel.ORIGINAL, rank=2, hazard_ids=["gdacs:FL:1"])
    alt = route(RouteLabel.ALTERNATIVE, duration_h=8.5, labels=[RouteLabel.ALTERNATIVE, RouteLabel.RECOMMENDED], rank=1)
    ev = evidence()
    pkg = package(
        req, [orig, alt], alerts=[alert(), alert("eonet:X", Severity.MINOR, official=False)], evidence_list=[ev]
    )
    dec = decision(
        req, pkg, ActionCode.CHANGE_ROUTE, selected=alt.route_id, risk=RiskLevel.HIGH, citations=[ev.evidence_id]
    )
    contacts, lim = directory.resolve("TH", locale="th")
    rec = build_response(create_request(req, pkg, dec), contacts=contacts, contact_limitations=lim, now=NOW)
    assert rec.action_code == ActionCode.CHANGE_ROUTE and rec.primary_route.route_id == alt.route_id
    assert [r.route_id for r in rec.alternatives] == [orig.route_id]
    assert rec.reasons == ["reason one", "reason two"]  # dedup, order kept
    assert rec.alerts[0].alert_id == "gdacs:FL:1" and rec.alerts[0].official  # official first
    assert rec.emergency_instructions and rec.emergency_instructions[0].citation_id == ev.evidence_id
    assert (
        rec.official_contacts and rec.official_contacts[0].phone == "191" and "ตำรวจ" in rec.official_contacts[0].label
    )
    assert all(c.source_url.startswith("https://") and c.verified_at for c in rec.official_contacts)
    assert rec.status == RunStatus.PARTIAL  # fallback explanation => degraded
    assert any("explanation fallback" in d for d in rec.degraded_services)
    assert rec.freshness.expires_at is not None and rec.expires_at <= NOW + timedelta(hours=1)
    assert rec.versions.policy == "1.0.0" and rec.locale == "th-TH"
    assert {s.provider for s in rec.sources} >= {"open_meteo", "openrouteservice", "gdacs"}


def test_builder_avoid_has_no_closed_primary(directory):
    req = travel_request()
    closed = route(RouteLabel.ORIGINAL, closed=True)
    pkg = package(req, [closed], alerts=[alert()])
    dec = decision(req, pkg, ActionCode.AVOID, risk=RiskLevel.HIGH)
    rec = build_response(create_request(req, pkg, dec), contacts=[], contact_limitations=[], now=NOW)
    assert rec.primary_route is None and rec.alternatives == []
    bad = decision(req, pkg, ActionCode.AVOID, selected=closed.route_id, risk=RiskLevel.HIGH)
    with pytest.raises(BuildError, match="AVOID_WITH_CLOSED_PRIMARY"):
        build_response(create_request(req, pkg, bad), contacts=[], contact_limitations=[], now=NOW)


def test_builder_rejects_change_route_without_usable_route_and_unknown_citation():
    req = travel_request()
    orig = route(RouteLabel.ORIGINAL)
    pkg = package(req, [orig])
    with pytest.raises(BuildError, match="CHANGE_ROUTE_WITHOUT_USABLE_ROUTE"):
        build_response(
            create_request(req, pkg, decision(req, pkg, ActionCode.CHANGE_ROUTE, selected=uuid4())),
            contacts=[],
            contact_limitations=[],
            now=NOW,
        )
    with pytest.raises(BuildError, match="UNKNOWN_CITATION"):
        build_response(
            create_request(req, pkg, decision(req, pkg, ActionCode.NORMAL, citations=[uuid4()])),
            contacts=[],
            contact_limitations=[],
            now=NOW,
        )


def test_builder_normal_completed_when_no_degradation():
    req = travel_request()
    pkg = package(req, [route(RouteLabel.ORIGINAL)])
    dec = decision(req, pkg, ActionCode.NORMAL, risk=RiskLevel.LOW, fallback=False)
    rec = build_response(
        create_request(req, pkg, dec), contacts=[], contact_limitations=["EMERGENCY_DIRECTORY_UNAVAILABLE:XX"], now=NOW
    )
    assert rec.status == RunStatus.COMPLETED and rec.emergency_instructions == []
    assert "EMERGENCY_DIRECTORY_UNAVAILABLE:XX" in rec.limitations and rec.primary_route is not None


# ------------------------------------------------------------------ directory
def test_directory_resolution_rules(directory):
    th, lim = directory.resolve("TH", locale="en-US")
    assert [c.phone for c in th][:3] == ["191", "1669", "199"] and not lim
    assert all(c.authority.value == "OFFICIAL" for c in th)
    bkk, _ = directory.resolve("TH", subdivision="Bangkok", service_types=["FIRE"])
    assert bkk and bkk[0].phone == "199"
    none_fire, _ = directory.resolve("TH", subdivision="Phuket", service_types=["FIRE"])
    assert none_fire == []  # Bangkok-only record must not leak to other subdivisions
    us, lim = directory.resolve("US")
    assert us == [] and lim == ["US/GENERAL_EMERGENCY: PENDING_VERIFICATION"]  # never shown as official
    xx, lim = directory.resolve("XX")
    assert xx == [] and lim == ["EMERGENCY_DIRECTORY_UNAVAILABLE:XX"]
    # expiry guard: after review_due_at the record is hidden
    future = datetime(2027, 6, 1, tzinfo=UTC)
    expired, lim = directory.resolve("TH", now=future)
    assert expired == [] and all("REVIEW_OVERDUE" in x for x in lim)
    jp, _ = directory.resolve("JP", locale="ja")
    assert {c.phone for c in jp} == {"110", "119"} and jp[0].label in ("警察", "消防・救急")


# ------------------------------------------------------------------ feedback
def test_feedback_sanitized_pseudonymous_and_unsafe_queued():
    from sta_contracts.enums import FeedbackCategory

    p = prepare(
        FeedbackCategory.UNSAFE,
        "call me at +66 81 234 5678, email a@b.co \x00" + "x" * 2000,
        user_scope="user-1",
        salt="s",
    )
    assert p.review_severity == "HIGH" and p.user_pseudonym != "user-1" and len(p.user_pseudonym) == 32
    assert "234" not in p.text_redacted and "a@b.co" not in p.text_redacted and len(p.text_redacted) <= 1000
    assert prepare(FeedbackCategory.HELPFUL, None, user_scope="u", salt="s").review_severity is None


# ------------------------------------------------------------------ alert rules
def _rec(action, risk, alerts=(), status=RunStatus.COMPLETED, trip_id=None):
    req = travel_request()
    if trip_id:
        req = req.model_copy(update={"trip_id": trip_id})
    alt = route(RouteLabel.ALTERNATIVE, duration_h=8.5, labels=[RouteLabel.ALTERNATIVE, RouteLabel.RECOMMENDED])
    pkg = package(req, [route(RouteLabel.ORIGINAL), alt], alerts=list(alerts))
    selected = alt.route_id if action == ActionCode.CHANGE_ROUTE else None
    dec = decision(req, pkg, action, risk=risk, fallback=False, selected=selected)
    rec = build_response(create_request(req, pkg, dec), contacts=[], contact_limitations=[], now=NOW)
    return rec.model_copy(update={"status": status})


def test_meaningful_change_rules():
    a = _rec(ActionCode.NORMAL, RiskLevel.LOW)
    b = _rec(ActionCode.NORMAL, RiskLevel.LOW)
    assert not assess_change(a, b).meaningful
    esc = assess_change(a, _rec(ActionCode.AVOID, RiskLevel.HIGH))
    assert (
        esc.meaningful
        and esc.escalation
        and esc.severity == Severity.SEVERE
        and esc.reasons[0].startswith("ACTION_ESCALATED")
    )
    closure = assess_change(a, _rec(ActionCode.NORMAL, RiskLevel.LOW, alerts=[alert("gdacs:TC:9", Severity.EXTREME)]))
    assert closure.meaningful and closure.severity == Severity.EXTREME and "NEW_OFFICIAL_ALERT" in closure.reasons[0]
    safer = assess_change(_rec(ActionCode.AVOID, RiskLevel.HIGH), _rec(ActionCode.CHANGE_ROUTE, RiskLevel.MEDIUM))
    assert safer.meaningful and not safer.escalation and safer.severity == Severity.MINOR
    initial = assess_change(None, _rec(ActionCode.DELAY, RiskLevel.MEDIUM))
    assert initial.meaningful and initial.reasons == ["INITIAL_DELAY"]
    assert not assess_change(None, _rec(ActionCode.NORMAL, RiskLevel.LOW)).meaningful
    # decrease is not an escalation
    down = assess_change(_rec(ActionCode.AVOID, RiskLevel.HIGH), _rec(ActionCode.DELAY, RiskLevel.MEDIUM))
    assert not down.escalation


def test_cooldown_never_suppresses_escalation():
    until = NOW + timedelta(minutes=30)
    assert not cooldown_allows(Severity.MODERATE, False, until, NOW)
    assert cooldown_allows(Severity.MODERATE, True, until, NOW)
    assert cooldown_allows(Severity.EXTREME, False, until, NOW)
    assert cooldown_allows(Severity.MODERATE, False, NOW - timedelta(minutes=1), NOW)


# ------------------------------------------------------------------ API end-to-end (in-memory repo)
@pytest.mark.asyncio
async def test_api_flow_recommendation_feedback_subscription_alerts():
    app = build(Settings(app_env="test"))
    req = travel_request()
    orig = route(RouteLabel.ORIGINAL)
    pkg = package(req, [orig])
    dec = decision(req, pkg, ActionCode.NORMAL, risk=RiskLevel.LOW, fallback=False)
    body = create_request(req, pkg, dec).model_dump(mode="json")
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r1 = await c.post("/internal/v1/recommendations", json=body)
            assert r1.status_code == 201, r1.text
            rec1 = r1.json()["data"]
            assert rec1["official_contacts"][0]["phone"] == "191" and rec1["action_code"] == "NORMAL"
            # idempotent on (request, decision)
            assert (await c.post("/internal/v1/recommendations", json=body)).json()["data"][
                "recommendation_id"
            ] == rec1["recommendation_id"]
            # ownership: other user cannot read
            other = await c.get(
                f"/internal/v1/recommendations/{rec1['recommendation_id']}", params={"user_scope": "someone-else-xyz"}
            )
            assert other.status_code == 404
            mine = await c.get(
                f"/internal/v1/recommendations/{rec1['recommendation_id']}", params={"user_scope": "user-scope-abc123"}
            )
            assert mine.status_code == 200
            # feedback UNSAFE -> review queue, explicit only, no retraining hook
            fb = await c.post(
                "/internal/v1/feedback",
                json={
                    "user_scope": "user-scope-abc123",
                    "recommendation_id": rec1["recommendation_id"],
                    "category": "UNSAFE",
                    "text": "route was flooded, call +66812345678",
                },
            )
            assert (
                fb.status_code == 201
                and fb.json()["data"]["review_status"] == "QUEUED"
                and "812345678" not in fb.json()["data"]["text_redacted"]
            )
            queue = await c.get("/internal/v1/feedback/review-queue")
            assert len(queue.json()["data"]) == 1 and queue.json()["data"][0]["severity"] == "HIGH"
            wrong = await c.post(
                "/internal/v1/feedback",
                json={
                    "user_scope": "someone-else-xyz",
                    "recommendation_id": rec1["recommendation_id"],
                    "category": "HELPFUL",
                },
            )
            assert wrong.status_code == 404
            # subscription (in-app) with consent
            consent = str(uuid4())
            sub = await c.post(
                "/internal/v1/subscriptions",
                json={
                    "user_scope": "user-scope-abc123",
                    "trip_id": str(req.trip_id),
                    "channel": "IN_APP",
                    "consent_id": consent,
                },
            )
            assert sub.status_code == 201
            sub_id = sub.json()["data"]["id"]
            sms = await c.post(
                "/internal/v1/subscriptions",
                json={
                    "user_scope": "user-scope-abc123",
                    "trip_id": str(req.trip_id),
                    "channel": "SMS",
                    "consent_id": consent,
                },
            )
            assert sms.status_code == 422 and sms.json()["error"]["code"] == "UNSUPPORTED_COVERAGE"
            # new recommendation escalates to AVOID -> one delivery; repeat -> duplicate suppressed
            req2 = req.model_copy(update={"request_id": uuid4()})
            pkg2 = package(req2, [route(RouteLabel.ORIGINAL, closed=True)], alerts=[alert()])
            dec2 = decision(req2, pkg2, ActionCode.AVOID, risk=RiskLevel.HIGH, fallback=False)
            r2 = await c.post(
                "/internal/v1/recommendations",
                json=create_request(req2, pkg2, dec2, previous=rec1["recommendation_id"]).model_dump(mode="json"),
            )
            rec2 = r2.json()["data"]
            assert rec2["supersedes_recommendation_id"] == rec1["recommendation_id"]
            ev = await c.post(
                "/internal/v1/alerts/evaluate",
                json={
                    "new_recommendation_id": rec2["recommendation_id"],
                    "previous_recommendation_id": rec1["recommendation_id"],
                },
            )
            assert (
                ev.status_code == 200
                and ev.json()["data"][0]["decision"] == "DELIVERED"
                and ev.json()["data"][0]["severity"] == "SEVERE"
            )
            sent = app.state.channels["IN_APP"].sent
            assert len(sent) == 1 and "coordinates" not in str(sent[0]) and sent[0]["body"]["action_code"] == "AVOID"
            ev2 = await c.post(
                "/internal/v1/alerts/evaluate",
                json={
                    "new_recommendation_id": rec2["recommendation_id"],
                    "previous_recommendation_id": rec1["recommendation_id"],
                },
            )
            assert ev2.json()["data"][0]["decision"] == "SUPPRESSED_DUPLICATE" and len(sent) == 1
            # unchanged -> no notification
            ev3 = await c.post(
                "/internal/v1/alerts/evaluate",
                json={
                    "new_recommendation_id": rec2["recommendation_id"],
                    "previous_recommendation_id": rec2["recommendation_id"],
                },
            )
            assert ev3.json()["data"][0]["decision"] == "NO_CHANGE"
            # revoke consent stops everything
            rv = await c.post("/internal/v1/subscriptions/revoke-consent", json={"consent_id": consent})
            assert rv.json()["data"]["revoked"] == 1
            ev4 = await c.post(
                "/internal/v1/alerts/evaluate",
                json={
                    "new_recommendation_id": rec2["recommendation_id"],
                    "previous_recommendation_id": rec1["recommendation_id"],
                },
            )
            assert ev4.json()["data"] == []
            gone = await c.delete(f"/internal/v1/subscriptions/{sub_id}", params={"user_scope": "someone-else-xyz"})
            assert gone.status_code == 404
            contacts = await c.get("/internal/v1/emergency/contacts", params={"country_code": "th", "locale": "th"})
            assert contacts.status_code == 200 and contacts.json()["data"]["contacts"][0]["phone"] == "191"
            assert (await c.get("/internal/v1/emergency/contacts", params={"country_code": "US"})).json()["data"][
                "contacts"
            ] == []


def test_partial_status_when_data_stale():
    req = travel_request()
    pkg = package(req, [route(RouteLabel.ORIGINAL)], status=DataStatus.STALE)
    dec = decision(req, pkg, ActionCode.NORMAL, risk=RiskLevel.LOW, fallback=False)
    rec = build_response(create_request(req, pkg, dec), contacts=[], contact_limitations=[], now=NOW)
    assert rec.status == RunStatus.PARTIAL
