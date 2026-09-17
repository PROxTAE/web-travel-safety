"""Downstream services are stubbed with respx; each stub builds a contract-valid response from the request body
so ids chain exactly like the real services (request -> snapshot -> package -> decision -> recommendation)."""

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
import pytest
import respx
from sta_contracts.enums import (
    ActionCode,
    DataStatus,
    QualityGate,
    RiskLevel,
    RouteLabel,
    RunStatus,
    SourceAuthority,
    TravelMode,
)
from sta_contracts.geo import LineString, Point
from sta_contracts.models import (
    ConflictSummary,
    DataQuality,
    DecisionResult,
    DecisionValidation,
    EvidencePackage,
    ExternalContext,
    Freshness,
    IntegratedTravelContext,
    LocationRef,
    ModelRef,
    ProviderHealth,
    QualitySummary,
    RecommendationResponse,
    RiskAssessment,
    RouteCandidate,
    RouteSegment,
    SourceProvenance,
    TravelPreference,
    TravelRequest,
    TravelWindow,
    VersionInfo,
    WeatherForecastPoint,
)

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SERVICE_AUTH_TOKEN", "")

NOW = datetime.now(UTC)
BKK = [100.5018, 13.7563]
CNX = [98.9853, 18.7883]
URLS = {
    "external-data": "http://ext.test",
    "data-integration": "http://di.test",
    "risk-knowledge": "http://rk.test",
    "decision-engine": "http://de.test",
    "recommendation": "http://rec.test",
}


def loc(coords, name, confirmed=True):
    return LocationRef(
        place_id=name,
        display_name=name,
        coordinates=Point(coordinates=coords),
        country_code="TH",
        timezone="Asia/Bangkok",
        provider="open_meteo_geocoding",
        confirmed_by_user=confirmed,
    )


def travel_request(
    question=None, confirmed=True, conversation_id=None, intent_hint=None, request_id=None, trip_id=None
):
    return TravelRequest(
        request_id=request_id or uuid4(),
        trip_id=trip_id or uuid4(),
        conversation_id=conversation_id,
        user_scope_hash="user-scope-abc123",
        origin=loc(BKK, "Bangkok", confirmed),
        destination=loc(CNX, "Chiang Mai", confirmed),
        departure_time=NOW + timedelta(hours=6),
        travel_modes=[TravelMode.CAR],
        preferences=TravelPreference(),
        question=question,
        intent_hint=intent_hint,
        locale="th-TH",
        timezone="Asia/Bangkok",
    )


def src(provider, rid):
    return SourceProvenance(
        source_id=uuid5(NAMESPACE_URL, f"{provider}:{rid}"),
        provider=provider,
        provider_record_id=rid,
        authority=SourceAuthority.LICENSED_PROVIDER,
        source_url=f"https://{provider}.example/{rid}",
        observed_at=NOW,
        fetched_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        content_hash=hashlib.sha256(rid.encode()).hexdigest(),
    )


def route():
    line = LineString(coordinates=[BKK, CNX])
    return RouteCandidate(
        route_id=uuid5(NAMESPACE_URL, "route:orig"),
        label=RouteLabel.ORIGINAL,
        labels=[RouteLabel.ORIGINAL],
        mode=TravelMode.CAR,
        geometry=line,
        segments=[
            RouteSegment(index=0, mode=TravelMode.CAR, geometry=line, distance_m=580_000, duration_seconds=8 * 3600)
        ],
        distance_m=580_000,
        duration_seconds=8 * 3600,
        quality=DataQuality(score=0.9),
        sources=[src("openrouteservice", "r1")],
    )


class Stubs:
    """Records calls and lets tests inject failures per service."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {k: 0 for k in URLS}
        self.fail: dict[str, httpx.Response | None] = {k: None for k in URLS}
        self.action = ActionCode.NORMAL
        self.degraded_ext: list[str] = []
        self.decision_summary = "ok"

    def _env(self, data, status=200, degraded=None):
        return httpx.Response(
            status,
            json={
                "data": data,
                "meta": {
                    "request_id": "x",
                    "correlation_id": "y",
                    "contract_version": "1.0.0",
                    "generated_at": NOW.isoformat(),
                    "degraded_services": degraded or [],
                },
            },
        )

    def external(self, request: httpx.Request) -> httpx.Response:
        self.calls["external-data"] += 1
        if self.fail["external-data"]:
            return self.fail["external-data"]
        body = json.loads(request.content)
        ctx = ExternalContext(
            context_id=uuid4(),
            request_id=body["request_id"],
            routes=[route()],
            weather=[
                WeatherForecastPoint(
                    id=uuid4(),
                    location=Point(coordinates=BKK),
                    valid_at=NOW,
                    route_sample_index=0,
                    temperature_c=30.0,
                    precipitation_mm=0.0,
                    wind_gust_kmh=10.0,
                    quality=DataQuality(score=0.9),
                    source=src("open_meteo", "w1"),
                )
            ],
            transport=[],
            disaster_events=[],
            official_alerts=[],
            provider_health=[
                ProviderHealth(provider="open_meteo", kind="WEATHER", status="UP", enabled=True, checked_at=NOW)
            ],
            degraded_services=self.degraded_ext,
            unavailable_capabilities=["openrouteservice: ORS_API_KEY not configured"],
            fetched_at=NOW,
        )
        return self._env(json.loads(ctx.model_dump_json()), degraded=self.degraded_ext)

    def integration(self, request: httpx.Request) -> httpx.Response:
        self.calls["data-integration"] += 1
        if self.fail["data-integration"]:
            return self.fail["data-integration"]
        body = json.loads(request.content)
        tr = body["travel_request"]
        q = DataQuality(status=DataStatus.FRESH, score=0.85, coverage=1.0, completeness=0.9, freshness_seconds=60)
        snap = IntegratedTravelContext(
            snapshot_id=uuid4(),
            request_id=tr["request_id"],
            trip_id=tr["trip_id"],
            trip_revision=tr.get("trip_revision", 1),
            travel_window=TravelWindow(
                departure_at=tr["departure_time"],
                arrival_at=datetime.fromisoformat(tr["departure_time"]) + timedelta(hours=8),
                timezone="Asia/Bangkok",
            ),
            travel_modes=[TravelMode.CAR],
            route_candidates=[route()],
            weather=[],
            transport=[],
            disaster_events=[],
            official_alerts=[],
            features={
                "route_distance_km": 580.0,
                "weather_max_precip_mm": 0.0,
                "weather_max_wind_gust_kmh": 10.0,
                "weather_max_temperature_c": 30.0,
                "weather_min_temperature_c": 24.0,
                "weather_coverage_ratio": 1.0,
            },
            quality_summary=QualitySummary(
                gate=QualityGate.PASS, overall=q, weather=q, transport=q, disaster=q, route=q
            ),
            conflict_summary=ConflictSummary(),
            source_ids=[],
            created_at=NOW,
            content_hash="a" * 64,
        )
        return self._env(json.loads(snap.model_dump_json()), status=201)

    def risk(self, request: httpx.Request) -> httpx.Response:
        self.calls["risk-knowledge"] += 1
        if self.fail["risk-knowledge"]:
            return self.fail["risk-knowledge"]
        body = json.loads(request.content)
        snap = body["snapshot"]
        r = route()
        pkg = EvidencePackage(
            package_id=uuid4(),
            request_id=body.get("request_id") or snap["request_id"],  # mirrors risk-knowledge
            snapshot_id=snap["snapshot_id"],
            risk_assessments=[
                RiskAssessment(
                    assessment_id=uuid4(),
                    snapshot_id=snap["snapshot_id"],
                    route_id=r.route_id,
                    score=0.1,
                    probability_high=0.1,
                    risk_level=RiskLevel.LOW,
                    uncertainty=0.2,
                    model=ModelRef(name="rule-baseline", version="rule-1.0.0", feature_schema_version="1.0.0"),
                    quality=DataQuality(score=0.9),
                    created_at=NOW,
                )
            ],
            routes=[r],
            evidence=[],
            quality_summary=QualitySummary.model_validate(snap["quality_summary"]),
            conflict_summary=ConflictSummary(),
            official_alerts=[],
            limitations=["MODEL_UNAVAILABLE"],
            degraded_services=["risk-knowledge: model unavailable (rule baseline)"],
            knowledge_collection_version="2026.09.1",
            created_at=NOW,
        )
        return self._env(json.loads(pkg.model_dump_json()), degraded=pkg.degraded_services)

    def decision(self, request: httpx.Request) -> httpx.Response:
        self.calls["decision-engine"] += 1
        if self.fail["decision-engine"]:
            return self.fail["decision-engine"]
        body = json.loads(request.content)
        pkg = body["evidence_package"]
        d = DecisionResult(
            decision_id=uuid4(),
            request_id=body["travel_request"]["request_id"],  # mirrors decision-engine (keyed by the current request)
            snapshot_id=pkg["snapshot_id"],
            action_code=self.action,
            risk_level=RiskLevel.LOW,
            confidence=0.7,
            selected_route_id=route().route_id,
            rules_fired=["LOW_NORMAL"],
            summary=self.decision_summary,
            versions=VersionInfo(policy="1.0.0", prompt="1.0.0", contract="1.0.0"),
            validation=DecisionValidation(used_fallback=True, fallback_reason="llm_disabled"),
            locale=body.get("locale", "en-US"),
            created_at=NOW,
        )
        return self._env(json.loads(d.model_dump_json()), status=201)

    def recommendation(self, request: httpx.Request) -> httpx.Response:
        self.calls["recommendation"] += 1
        if self.fail["recommendation"]:
            return self.fail["recommendation"]
        body = json.loads(request.content)
        d = body["decision"]
        rec = RecommendationResponse(
            recommendation_id=uuid4(),
            request_id=body["travel_request"]["request_id"],
            trip_id=body["travel_request"]["trip_id"],
            decision_id=d["decision_id"],
            snapshot_id=d["snapshot_id"],
            status=RunStatus.COMPLETED,
            action_code=d["action_code"],
            risk_level=d["risk_level"],
            confidence=0.7,
            short_summary=d["summary"],
            primary_route=route(),
            freshness=Freshness(fetched_at=NOW),
            versions=VersionInfo(policy="1.0.0", contract="1.0.0"),
            created_at=NOW,
            supersedes_recommendation_id=body.get("previous_recommendation_id"),
        )
        return self._env(json.loads(rec.model_dump_json()), status=201)

    def mount(self, router: respx.MockRouter) -> None:
        router.post("http://ext.test/internal/v1/context/query").mock(side_effect=self.external)
        router.post("http://di.test/internal/v1/snapshots").mock(side_effect=self.integration)
        router.post("http://rk.test/internal/v1/evidence/package").mock(side_effect=self.risk)
        router.post("http://de.test/internal/v1/decisions").mock(side_effect=self.decision)
        router.post("http://rec.test/internal/v1/recommendations").mock(side_effect=self.recommendation)


@pytest.fixture
def stubs():
    return Stubs()


@pytest.fixture
def settings():
    from app.settings import Settings

    return Settings(
        app_env="test",
        external_data_service_url=URLS["external-data"],
        data_integration_service_url=URLS["data-integration"],
        risk_knowledge_service_url=URLS["risk-knowledge"],
        decision_service_url=URLS["decision-engine"],
        recommendation_service_url=URLS["recommendation"],
        agent_total_timeout_seconds=20,
    )
