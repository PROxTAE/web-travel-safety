"""Test harness: a local RSA key pair acts as the Keycloak realm key (served through respx as the JWKS document);
agent / external-data / recommendation are respx stubs that answer with contract-valid envelopes."""

import base64
import json
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from sta_contracts.enums import (
    ActionCode,
    DataStatus,
    RiskLevel,
    RouteLabel,
    RunStatus,
    SourceAuthority,
    TravelMode,
)
from sta_contracts.geo import LineString
from sta_contracts.models import (
    DataQuality,
    Freshness,
    RecommendationResponse,
    RouteCandidate,
    RouteSegment,
    SourceProvenance,
    VersionInfo,
)

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SERVICE_AUTH_TOKEN", "")

ISSUER = "http://kc.test/realms/smart-travel"
AUD = "smart-travel-api"
NOW = datetime.now(UTC)
BKK = [100.5018, 13.7563]
CNX = [98.9853, 18.7883]
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "test-kid-1"


def _b64(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def jwks_doc(key: Any = KEY, kid: str = KID) -> dict[str, Any]:
    pub = key.public_key().public_numbers()
    return {"keys": [{"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256", "n": _b64(pub.n), "e": _b64(pub.e)}]}


def token(
    sub: str = "user-1",
    *,
    roles: tuple[str, ...] = ("traveler",),
    exp_delta: int = 600,
    iss: str = ISSUER,
    aud: str | list[str] = AUD,
    key: Any = KEY,
    kid: str = KID,
    name: str = "Test Traveler",
) -> str:
    now = int(time.time())
    claims = {
        "iss": iss,
        "sub": sub,
        "aud": aud,
        "exp": now + exp_delta,
        "iat": now - 5,
        "nbf": now - 5,
        "realm_access": {"roles": list(roles)},
        "scope": "openid profile",
        "name": name,
        "email_verified": True,
    }
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def auth(sub: str = "user-1", **kw: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(sub, **kw)}"}


def location(coords: list[float], name: str, confirmed: bool = True) -> dict[str, Any]:
    return {
        "place_id": name,
        "display_name": name,
        "coordinates": {"type": "Point", "coordinates": coords},
        "country_code": "TH",
        "timezone": "Asia/Bangkok",
        "provider": "open_meteo_geocoding",
        "confirmed_by_user": confirmed,
    }


def trip_body(confirmed: bool = True, **over: Any) -> dict[str, Any]:
    body = {
        "origin": location(BKK, "Bangkok", confirmed),
        "destination": location(CNX, "Chiang Mai", confirmed),
        "departure_time": (NOW + timedelta(hours=6)).isoformat(),
        "travel_modes": ["CAR"],
        "timezone": "Asia/Bangkok",
    }
    body.update(over)
    return body


def _env(data: Any, status: int = 200, degraded: list[str] | None = None) -> httpx.Response:
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


def _route() -> RouteCandidate:
    line = LineString(coordinates=[BKK, CNX])
    return RouteCandidate(
        route_id=uuid5(NAMESPACE_URL, "route:orig"),
        label=RouteLabel.ORIGINAL,
        labels=[RouteLabel.ORIGINAL],
        mode=TravelMode.CAR,
        geometry=line,
        segments=[
            RouteSegment(index=0, mode=TravelMode.CAR, geometry=line, distance_m=580_000, duration_seconds=28800)
        ],
        distance_m=580_000,
        duration_seconds=28800,
        quality=DataQuality(status=DataStatus.FRESH, score=0.9),
        sources=[
            SourceProvenance(
                source_id=uuid5(NAMESPACE_URL, "src:r1"),
                provider="openrouteservice",
                provider_record_id="r1",
                authority=SourceAuthority.LICENSED_PROVIDER,
                source_url="https://ors.example/r1",
                observed_at=NOW,
                fetched_at=NOW,
                expires_at=NOW + timedelta(hours=1),
                content_hash="a" * 64,
            )
        ],
    )


ROUTE_ID = _route().route_id


class Stubs:
    """Agent + downstream doubles. The agent double keeps run state and advances it on each poll."""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.recs: dict[str, dict[str, Any]] = {}
        self.calls: dict[str, int] = {}
        self.fail: dict[str, httpx.Response | None] = {}
        self.agent_final = "COMPLETED"
        self.agent_needs_input = False
        self.subscriptions: dict[str, dict[str, Any]] = {}
        self.jwks_calls = 0
        self.jwks = jwks_doc()

    def _count(self, name: str) -> httpx.Response | None:
        self.calls[name] = self.calls.get(name, 0) + 1
        return self.fail.get(name)

    # ---------------------------------------------------------------- OIDC
    def jwks_handler(self, request: httpx.Request) -> httpx.Response:
        self.jwks_calls += 1
        return httpx.Response(200, json=self.jwks)

    # ---------------------------------------------------------------- agent
    def agent_start(self, request: httpx.Request) -> httpx.Response:
        f = self._count("agent.start")
        if f:
            return f
        body = json.loads(request.content)
        tr = body["travel_request"]
        rid = tr["request_id"]
        if rid in self.runs:
            return httpx.Response(409, json={"error": {"code": "CONFLICT", "message": "exists"}})
        self.runs[rid] = {
            "request_id": rid,
            "trip_id": tr["trip_id"],
            "conversation_id": body.get("conversation_id"),
            "status": "QUEUED",
            "user_scope_hash": tr.get("user_scope_hash"),
            "question": tr.get("question"),
            "polls": 0,
            "events": [
                {
                    "event_id": 1,
                    "event_type": "run.accepted",
                    "request_id": rid,
                    "status": "QUEUED",
                    "occurred_at": NOW.isoformat(),
                }
            ],
        }
        return _env(
            {
                "request_id": rid,
                "status": "QUEUED",
                "events_url": f"/internal/v1/runs/{rid}/events",
                "poll_url": f"/internal/v1/runs/{rid}",
                "submitted_at": NOW.isoformat(),
                "conversation_id": body.get("conversation_id"),
            },
            202,
        )

    def _advance(self, run: dict[str, Any]) -> None:
        run["polls"] += 1
        rid = run["request_id"]
        if run["status"] == "QUEUED":
            run["status"] = "RUNNING"
            run["events"].append(
                {
                    "event_id": 2,
                    "event_type": "run.progress",
                    "request_id": rid,
                    "status": "RUNNING",
                    "stage": "FETCHING_EXTERNAL_DATA",
                    "message_key": "progress.fetching_external_data",
                    "occurred_at": NOW.isoformat(),
                }
            )
            return
        if run["status"] == "RUNNING":
            if self.agent_needs_input:
                run["status"] = "NEEDS_INPUT"
                run["events"].append(
                    {
                        "event_id": 3,
                        "event_type": "run.needs_input",
                        "request_id": rid,
                        "status": "NEEDS_INPUT",
                        "missing_fields": ["origin.confirmed_by_user"],
                        "prompt_key": "needs_input.confirm_locations",
                        "occurred_at": NOW.isoformat(),
                    }
                )
                return
            rec_id = str(uuid4())
            self.recs[rec_id] = self._recommendation(rec_id, run)
            run["status"] = self.agent_final
            run["recommendation_id"] = rec_id
            run["events"].append(
                {
                    "event_id": 3,
                    "event_type": "run.completed",
                    "request_id": rid,
                    "status": self.agent_final,
                    "recommendation_id": rec_id,
                    "occurred_at": NOW.isoformat(),
                }
            )

    def _recommendation(self, rec_id: str, run: dict[str, Any]) -> dict[str, Any]:
        rec = RecommendationResponse(
            recommendation_id=UUID(rec_id),
            request_id=UUID(run["request_id"]),
            trip_id=UUID(run["trip_id"]),
            decision_id=uuid4(),
            snapshot_id=uuid4(),
            status=RunStatus.COMPLETED,
            action_code=ActionCode.NORMAL,
            risk_level=RiskLevel.LOW,
            confidence=0.7,
            short_summary="Conditions look normal.",
            primary_route=_route(),
            freshness=Freshness(fetched_at=NOW),
            versions=VersionInfo(policy="1.0.0", contract="1.0.0"),
            degraded_services=["risk-knowledge: model unavailable (rule baseline)"],
            created_at=NOW,
        )
        return {"owner": run.get("user_scope_hash"), "body": json.loads(rec.model_dump_json())}

    def agent_state(self, request: httpx.Request) -> httpx.Response:
        f = self._count("agent.state")
        if f:
            return f
        rid = request.url.path.rsplit("/", 1)[-1]
        run = self.runs.get(rid)
        if run is None:
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "run not found"}})
        self._advance(run)
        return _env(
            {
                "request_id": rid,
                "trip_id": run["trip_id"],
                "conversation_id": run.get("conversation_id"),
                "status": run["status"],
                "stage": "FETCHING_EXTERNAL_DATA" if run["status"] == "RUNNING" else None,
                "missing_fields": ["origin.confirmed_by_user"] if run["status"] == "NEEDS_INPUT" else [],
                "recommendation_id": run.get("recommendation_id"),
                "degraded_services": ["risk-knowledge: model unavailable (rule baseline)"],
                "versions": {"policy": "1.0.0", "contract": "1.0.0", "graph": "1.0.0+abc"},
                "started_at": NOW.isoformat(),
                "updated_at": NOW.isoformat(),
            }
        )

    def agent_events(self, request: httpx.Request) -> httpx.Response:
        rid = request.url.path.split("/")[-2]
        run = self.runs.get(rid)
        if run is None:
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "run not found"}})
        self._advance(run)
        return _env(run["events"])

    def agent_cancel(self, request: httpx.Request) -> httpx.Response:
        self._count("agent.cancel")
        rid = request.url.path.split("/")[-2]
        run = self.runs.get(rid)
        if run is None:
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "run not found"}})
        run["status"] = "CANCELLED"
        return _env({"request_id": rid, "status": "CANCELLED"})

    # ---------------------------------------------------------------- external-data
    def geocode(self, request: httpx.Request) -> httpx.Response:
        f = self._count("geocode")
        if f:
            return f
        body = json.loads(request.content)
        return _env([location(CNX, f"{body['query']} (match)", confirmed=False)])

    def disasters(self, request: httpx.Request) -> httpx.Response:
        self._count("disasters")
        return _env({"events": [], "official_alerts": [], "bbox": [98, 13, 101, 19]}, degraded=["gdacs: timeout"])

    def nearby(self, request: httpx.Request) -> httpx.Response:
        self._count("nearby")
        body = json.loads(request.content)
        self.last_nearby = body
        return _env({"type": "FeatureCollection", "features": []})

    # ---------------------------------------------------------------- recommendation
    def rec_get(self, request: httpx.Request) -> httpx.Response:
        f = self._count("rec.get")
        if f:
            return f
        rec_id = request.url.path.rsplit("/", 1)[-1]
        rec = self.recs.get(rec_id)
        scope = request.url.params.get("user_scope")
        if rec is None or (rec["owner"] and scope and _pseudo(scope) != _pseudo(rec["owner"])):
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "recommendation not found"}})
        return _env(rec["body"], degraded=rec["body"]["degraded_services"])

    def feedback(self, request: httpx.Request) -> httpx.Response:
        self._count("feedback")
        body = json.loads(request.content)
        self.last_feedback = body
        return _env(
            {
                "id": str(uuid4()),
                "recommendation_id": body["recommendation_id"],
                "category": body["category"],
                "text_redacted": (body.get("text") or "").replace("0812345678", "[phone]") or None,
                "review_status": "QUEUED" if body["category"] == "UNSAFE" else "NONE",
                "created_at": NOW.isoformat(),
            },
            201,
        )

    def subscribe(self, request: httpx.Request) -> httpx.Response:
        self._count("subscribe")
        body = json.loads(request.content)
        sid = str(uuid4())
        self.subscriptions[sid] = body
        return _env(
            {
                "id": sid,
                "trip_id": body["trip_id"],
                "channel": body["channel"],
                "consent_id": body["consent_id"],
                "status": "ACTIVE",
                "severity_threshold": body["severity_threshold"],
                "locale": body["locale"],
                "created_at": NOW.isoformat(),
            },
            201,
        )

    def unsubscribe(self, request: httpx.Request) -> httpx.Response:
        self._count("unsubscribe")
        sid = request.url.path.rsplit("/", 1)[-1]
        sub = self.subscriptions.get(sid)
        if sub is None or sub["user_scope"] != request.url.params.get("user_scope"):
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "subscription not found"}})
        return httpx.Response(204)

    def revoke_consent(self, request: httpx.Request) -> httpx.Response:
        self._count("revoke_consent")
        return _env({"revoked": 1})

    def alerts_evaluate(self, request: httpx.Request) -> httpx.Response:
        self._count("alerts_evaluate")
        return _env([])

    def contacts(self, request: httpx.Request) -> httpx.Response:
        self._count("contacts")
        cc = request.url.params.get("country_code")
        return _env(
            {
                "contacts": [
                    {
                        "country_code": cc,
                        "service_type": "MEDICAL",
                        "label": "Emergency Medical Services",
                        "phone": "1669",
                        "authority": "OFFICIAL",
                        "source_url": "https://www.niems.go.th/",
                        "verified_at": NOW.isoformat(),
                        "effective_at": NOW.isoformat(),
                    }
                ],
                "limitations": [],
                "directory_version": "2026.09.1",
            }
        )

    def mount(self, router: respx.MockRouter) -> None:
        router.get(f"{ISSUER}/protocol/openid-connect/certs").mock(side_effect=self.jwks_handler)
        router.post("http://agent.test/internal/v1/runs").mock(side_effect=self.agent_start)
        router.get(url__regex=r"http://agent\.test/internal/v1/runs/[^/]+/events").mock(side_effect=self.agent_events)
        router.post(url__regex=r"http://agent\.test/internal/v1/runs/[^/]+/cancel").mock(side_effect=self.agent_cancel)
        router.get(url__regex=r"http://agent\.test/internal/v1/runs/[^/]+$").mock(side_effect=self.agent_state)
        router.get("http://agent.test/health/live").mock(return_value=httpx.Response(200, json={"status": "ok"}))
        router.post("http://ext.test/internal/v1/geocode/search").mock(side_effect=self.geocode)
        router.post("http://ext.test/internal/v1/disasters/query").mock(side_effect=self.disasters)
        router.post("http://ext.test/internal/v1/places/nearby").mock(side_effect=self.nearby)
        router.get(url__regex=r"http://rec\.test/internal/v1/recommendations/.*").mock(side_effect=self.rec_get)
        router.post("http://rec.test/internal/v1/feedback").mock(side_effect=self.feedback)
        router.post("http://rec.test/internal/v1/subscriptions").mock(side_effect=self.subscribe)
        router.delete(url__regex=r"http://rec\.test/internal/v1/subscriptions/.*").mock(side_effect=self.unsubscribe)
        router.post("http://rec.test/internal/v1/subscriptions/revoke-consent").mock(side_effect=self.revoke_consent)
        router.post("http://rec.test/internal/v1/alerts/evaluate").mock(side_effect=self.alerts_evaluate)
        router.get(url__regex=r"http://rec\.test/internal/v1/emergency/contacts.*").mock(side_effect=self.contacts)


def _pseudo(scope: str) -> str:
    return scope


@pytest.fixture
def stubs() -> Stubs:
    return Stubs()


@pytest.fixture
def settings():
    from app.settings import Settings

    return Settings(
        app_env="test",
        oidc_issuer=ISSUER,
        oidc_internal_issuer=None,
        oidc_audience=AUD,
        agent_service_url="http://agent.test",
        external_data_service_url="http://ext.test",
        recommendation_service_url="http://rec.test",
        emergency_profile_encryption_key=base64.b64encode(b"k" * 32).decode(),
        user_scope_salt="test-salt",
        rate_limit_default_per_minute=1000,
        rate_limit_assessment_per_minute=100,
        rate_limit_search_per_minute=100,
        sse_heartbeat_seconds=0.2,
        sse_max_duration_seconds=5,
        sse_poll_interval_seconds=0.02,
        alerts_consumer_enabled=False,
    )


class Client:
    """AsyncClient wrapper that opens the ASGI lifespan and mounts the stubs into the active respx router."""

    def __init__(self, settings, stubs: Stubs) -> None:
        from app.main import build

        self.app = build(settings)
        self.stubs = stubs
        self.settings = settings

    async def __aenter__(self) -> "Client":
        self.stubs.mount(respx.mock)
        self._ls = self.app.router.lifespan_context(self.app)
        await self._ls.__aenter__()
        self.http = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://api.test")
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.http.aclose()
        await self._ls.__aexit__(None, None, None)

    async def create_trip(self, sub: str = "user-1", **over: Any) -> tuple[dict[str, Any], str]:
        r = await self.http.post("/api/v1/trips", json=trip_body(**over), headers=auth(sub))
        assert r.status_code == 201, r.text
        return r.json()["data"], r.headers["ETag"]

    async def wait_run(self, rid: str, sub: str = "user-1", tries: int = 10) -> dict[str, Any]:
        st: dict[str, Any] = {}
        for _ in range(tries):
            r = await self.http.get(f"/api/v1/runs/{rid}", headers=auth(sub))
            assert r.status_code == 200, r.text
            st = r.json()["data"]
            if st["status"] in ("COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "NEEDS_INPUT"):
                return st
        return st
