import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from sta_contracts.enums import (
    DataStatus,
    DisasterEventType,
    QualityGate,
    ReasonCode,
    RiskLevel,
    RouteLabel,
    Severity,
    SourceAuthority,
    TravelMode,
)
from sta_contracts.geo import LineString, Point
from sta_contracts.models import (
    ConflictSummary,
    DataQuality,
    DisasterEvent,
    EvidencePackage,
    LocationRef,
    ModelRef,
    QualitySummary,
    RetrievedEvidence,
    RiskAssessment,
    RouteCandidate,
    RouteExposure,
    SourceProvenance,
    TravelRequest,
)

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SERVICE_AUTH_TOKEN", "")

NOW = datetime(2026, 9, 20, 2, 0, tzinfo=UTC)
BKK = [100.5018, 13.7563]
CNX = [98.9853, 18.7883]


def loc(coords, name):
    return LocationRef(
        place_id=name,
        display_name=name,
        coordinates=Point(coordinates=coords),
        country_code="TH",
        timezone="Asia/Bangkok",
        provider="open_meteo_geocoding",
        confirmed_by_user=True,
    )


def travel_request(question=None, revision=1):
    return TravelRequest(
        request_id=uuid4(),
        trip_id=uuid4(),
        origin=loc(BKK, "Bangkok"),
        destination=loc(CNX, "Chiang Mai"),
        departure_time=NOW,
        travel_modes=[TravelMode.CAR],
        timezone="Asia/Bangkok",
        question=question,
        trip_revision=revision,
    )


def route(label=RouteLabel.ORIGINAL, duration_h=8.0, closed=False, hazard_ids=(), labels=None):
    line = LineString(coordinates=[BKK, CNX])
    r = RouteCandidate(
        route_id=uuid5(NAMESPACE_URL, f"r:{label}:{duration_h}:{closed}"),
        label=label,
        labels=labels if labels is not None else [label],
        mode=TravelMode.CAR,
        geometry=line,
        distance_m=580_000,
        duration_seconds=duration_h * 3600,
        exposure=RouteExposure(score=1.0 if closed else 0.0, hazard_event_ids=list(hazard_ids), closed=closed),
        usable=not closed,
        trade_offs=[f"{duration_h:.1f} h, 580 km"],
        quality=DataQuality(score=0.9),
    )
    return r


def assessment(r, snapshot_id, p, level, uncertainty=0.2, time_dependent=False, later=None, delay=None, reasons=()):
    return RiskAssessment(
        assessment_id=uuid4(),
        snapshot_id=snapshot_id,
        route_id=r.route_id,
        score=p,
        probability_high=p,
        risk_level=level,
        uncertainty=uncertainty,
        reason_codes=list(reasons),
        time_dependent=time_dependent,
        later_window_risk_level=later,
        later_window_delay_minutes=delay,
        model=ModelRef(name="route-risk-baseline", version="1.0.0", feature_schema_version="1.0.0"),
        quality=DataQuality(score=0.9),
        created_at=NOW,
    )


def evidence(
    doc="fema-ready-floods",
    passage="Turn Around, Don't Drown! Do not drive through flood waters.",
    expires=NOW + timedelta(days=100),
):
    return RetrievedEvidence(
        evidence_id=uuid5(NAMESPACE_URL, f"ev:{doc}:{passage[:10]}"),
        document_id=doc,
        authority=SourceAuthority.OFFICIAL,
        title=doc,
        source_url="https://www.ready.gov/floods",
        section="During a Flood",
        language="en",
        hazards=[DisasterEventType.FLOOD],
        effective_at=NOW - timedelta(days=100),
        expires_at=expires,
        passage=passage,
        retrieval_score=0.8,
        collection_version="2026.09.1",
        content_hash=hashlib.sha256(passage.encode()).hexdigest(),
    )


def official_alert(eid="gdacs:FL:1", closure=False):
    src = SourceProvenance(
        source_id=uuid5(NAMESPACE_URL, eid),
        provider="gdacs",
        authority=SourceAuthority.INTERGOVERNMENTAL,
        fetched_at=NOW,
        content_hash="b" * 64,
    )
    return DisasterEvent(
        event_id=eid,
        event_type=DisasterEventType.FLOOD,
        title="Flood",
        severity=Severity.SEVERE,
        geometry=Point(coordinates=[99.7, 16.2]),
        effective_at=NOW - timedelta(hours=2),
        official=True,
        closure=closure,
        quality=DataQuality(score=0.9),
        source=src,
    )


def package(
    req,
    *,
    routes,
    assessments,
    gate=QualityGate.PASS,
    conflicts=0,
    evidence_list=None,
    alerts=None,
    coverage=1.0,
    quality=0.9,
    limitations=None,
    facts=None,
):
    q = DataQuality(
        status=DataStatus.FRESH if gate == QualityGate.PASS else DataStatus.PARTIAL,
        score=quality,
        coverage=coverage,
        completeness=0.9,
        freshness_seconds=60,
    )
    return EvidencePackage(
        package_id=uuid4(),
        request_id=req.request_id,
        snapshot_id=assessments[0].snapshot_id,
        trip_revision=req.trip_revision,
        risk_assessments=assessments,
        routes=routes,
        evidence=evidence_list or [],
        quality_summary=QualitySummary(gate=gate, overall=q, weather=q, transport=q, disaster=q, route=q),
        conflict_summary=ConflictSummary(count=conflicts, safety_critical=conflicts),
        official_alerts=alerts or [],
        weather_facts=facts
        or ["Max hourly precipitation along corridor: 42.0 mm", "Max wind gust along corridor: 70 km/h"],
        limitations=limitations or [],
        knowledge_collection_version="2026.09.1",
        created_at=NOW,
    )


@pytest.fixture
def policy():
    from app.policy.loader import load_policy

    return load_policy("policies/v1/decision-table.yaml", "schemas/decision-policy.schema.json")


__all__ = ["ReasonCode", "RiskLevel"]
