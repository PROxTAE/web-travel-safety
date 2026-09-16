import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from sta_contracts.enums import (
    ActionCode,
    DataStatus,
    DisasterEventType,
    QualityGate,
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
    DecisionResult,
    DecisionValidation,
    DisasterEvent,
    EvidencePackage,
    LocationRef,
    ModelRef,
    QualitySummary,
    RecommendationCreateRequest,
    RetrievedEvidence,
    RiskAssessment,
    RouteCandidate,
    RouteExposure,
    SourceProvenance,
    TravelRequest,
    VersionInfo,
)

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SERVICE_AUTH_TOKEN", "")

NOW = datetime(2026, 9, 20, 2, 0, tzinfo=UTC)
BKK = [100.5018, 13.7563]
CNX = [98.9853, 18.7883]


def loc(coords, name, cc="TH", admin1=None):
    return LocationRef(
        place_id=name,
        display_name=name,
        coordinates=Point(coordinates=coords),
        country_code=cc,
        admin1=admin1,
        timezone="Asia/Bangkok",
        provider="open_meteo_geocoding",
        confirmed_by_user=True,
    )


def travel_request(cc="TH", user="user-scope-abc123"):
    return TravelRequest(
        request_id=uuid4(),
        trip_id=uuid4(),
        user_scope_hash=user,
        origin=loc(BKK, "Bangkok", cc),
        destination=loc(CNX, "Chiang Mai", cc),
        departure_time=NOW,
        travel_modes=[TravelMode.CAR],
        timezone="Asia/Bangkok",
    )


def src(provider="usgs", rid="x", observed=NOW - timedelta(minutes=5), ttl=600):
    return SourceProvenance(
        source_id=uuid5(NAMESPACE_URL, f"{provider}:{rid}"),
        provider=provider,
        provider_record_id=rid,
        authority=SourceAuthority.OFFICIAL,
        source_url=f"https://{provider}.example/{rid}",
        observed_at=observed,
        fetched_at=NOW,
        expires_at=NOW + timedelta(seconds=ttl),
        content_hash=hashlib.sha256(rid.encode()).hexdigest(),
    )


def route(label=RouteLabel.ORIGINAL, closed=False, duration_h=8.0, labels=None, rank=None, hazard_ids=()):
    line = LineString(coordinates=[BKK, CNX])
    return RouteCandidate(
        route_id=uuid5(NAMESPACE_URL, f"r:{label}:{closed}:{duration_h}"),
        label=label,
        labels=labels if labels is not None else [label],
        mode=TravelMode.CAR,
        geometry=line,
        distance_m=580_000,
        duration_seconds=duration_h * 3600,
        exposure=RouteExposure(score=1.0 if closed else 0.1, closed=closed, hazard_event_ids=list(hazard_ids)),
        usable=not closed,
        rank=rank,
        trade_offs=[f"{duration_h:.1f} h"],
        quality=DataQuality(score=0.9),
        sources=[src("openrouteservice", f"route-{label}")],
    )


def alert(eid="gdacs:FL:1", severity=Severity.SEVERE, official=True):
    return DisasterEvent(
        event_id=eid,
        event_type=DisasterEventType.FLOOD,
        title="Flood " + eid,
        severity=severity,
        geometry=Point(coordinates=[99.7, 16.2]),
        effective_at=NOW - timedelta(hours=3),
        official=official,
        country_codes=["TH"],
        quality=DataQuality(score=0.9),
        source=src("gdacs", eid),
    )


def evidence(passage="• Do not drive through flood waters.\n• Move to higher ground if told to evacuate."):
    return RetrievedEvidence(
        evidence_id=uuid5(NAMESPACE_URL, "ev:" + passage[:12]),
        document_id="fema-ready-floods",
        authority=SourceAuthority.OFFICIAL,
        title="Floods — Ready.gov",
        source_url="https://www.ready.gov/floods",
        section="During a Flood",
        language="en",
        effective_at=NOW - timedelta(days=30),
        expires_at=NOW + timedelta(days=300),
        passage=passage,
        retrieval_score=0.8,
        collection_version="2026.09.1",
        content_hash=hashlib.sha256(passage.encode()).hexdigest(),
    )


def package(req, routes, *, alerts=(), evidence_list=(), degraded=(), status=DataStatus.FRESH, snapshot_id=None):
    snapshot_id = snapshot_id or uuid4()
    q = DataQuality(status=status, score=0.85, coverage=1.0, completeness=0.9, freshness_seconds=120)
    assessments = [
        RiskAssessment(
            assessment_id=uuid4(),
            snapshot_id=snapshot_id,
            route_id=r.route_id,
            score=0.8 if r.exposure.closed else 0.2,
            probability_high=0.8 if r.exposure.closed else 0.2,
            risk_level=RiskLevel.HIGH if r.exposure.closed else RiskLevel.LOW,
            uncertainty=0.2,
            model=ModelRef(name="rule-baseline", version="rule-1.0.0", feature_schema_version="1.0.0"),
            quality=q,
            created_at=NOW,
        )
        for r in routes
    ]
    return EvidencePackage(
        package_id=uuid4(),
        request_id=req.request_id,
        snapshot_id=snapshot_id,
        risk_assessments=assessments,
        routes=routes,
        evidence=list(evidence_list),
        quality_summary=QualitySummary(
            gate=QualityGate.PASS if status == DataStatus.FRESH else QualityGate.DEGRADED,
            overall=q,
            weather=q,
            transport=q,
            disaster=q,
            route=q,
        ),
        conflict_summary=ConflictSummary(),
        official_alerts=[a for a in alerts if a.official],
        weather_facts=["Max hourly precipitation along corridor: 12.0 mm"],
        limitations=[],
        degraded_services=list(degraded),
        knowledge_collection_version="2026.09.1",
        created_at=NOW,
    )


def decision(req, pkg, action, *, selected=None, risk=RiskLevel.MEDIUM, citations=(), fallback=True):
    return DecisionResult(
        decision_id=uuid4(),
        request_id=req.request_id,
        snapshot_id=pkg.snapshot_id,
        action_code=action,
        risk_level=risk,
        confidence=0.7,
        selected_route_id=selected,
        rules_fired=["TEST"],
        reason_codes=[],
        summary=f"summary for {action.value}",
        reasons=["reason one", "reason one", "reason two"],
        immediate_actions=["do this"],
        citations=list(citations),
        limitations=["L1"],
        versions=VersionInfo(policy="1.0.0", prompt="1.0.0", contract="1.0.0"),
        validation=DecisionValidation(used_fallback=fallback, fallback_reason="llm_disabled" if fallback else None),
        locale="th-TH",
        created_at=NOW,
    )


def create_request(req, pkg, dec, previous=None):
    return RecommendationCreateRequest(
        travel_request=req,
        decision=dec,
        evidence_package=pkg,
        snapshot_created_at=NOW,
        snapshot_sources=[src("open_meteo", "wx-1", ttl=3600)],
        weather_summary={"max_precip_mm": 12.0},
        previous_recommendation_id=previous,
    )


@pytest.fixture
def directory():
    from app.directory.resolver import Directory

    return Directory.load("emergency-directory/sources.yaml")


__all__ = ["ActionCode"]
