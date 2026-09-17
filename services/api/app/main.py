"""api service entry point (คน 2) — the public trust boundary.

Middleware order: body-size/request-context (sta_common) -> CORS -> security headers -> routers (auth, rate limit and
ownership are per-route dependencies so they run after validation of the path and before any handler logic).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncEngine
from sta_common.app import create_app
from sta_common.cache import make_redis
from sta_common.db import build_dsn, create_engine, ping, session_factory
from sta_common.health import HealthRegistry
from sta_common.http import ResilientClient
from sta_common.logging import get_logger

from app.api.v1 import facades, me, runs, trips
from app.application.alerts_consumer import ReassessmentConsumer
from app.application.assessments import AssessmentService
from app.application.sse import ConnectionGuard, EventBridge
from app.auth.jwt import JWKSCache, TokenVerifier
from app.clients.downstream import AgentClient, ExternalDataClient, RecommendationClient
from app.domain.crypto import ProfileCipher
from app.middleware.rate_limit import RateLimiter
from app.repositories.repo import Repository
from app.settings import Settings, get_settings

log = get_logger("api")
VERSION = "1.0.0"
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Permissions-Policy": "geolocation=(self), camera=(), microphone=()",
}


def _pinger(engine: AsyncEngine) -> Callable[[], Awaitable[None]]:
    async def check() -> None:
        await ping(engine)

    return check


def build_clients(s: Settings) -> dict[str, ResilientClient]:
    token = s.service_auth_token.get_secret_value() or None
    spec = {
        "agent": (s.agent_service_url, s.agent_submit_timeout_seconds),
        "external-data": (s.external_data_service_url, s.external_data_timeout_seconds),
        "recommendation": (s.recommendation_service_url, s.recommendation_timeout_seconds),
    }
    clients = {
        name: ResilientClient(
            service_name=s.service_name,
            dependency=name,
            base_url=url,
            connect_timeout=2,
            read_timeout=timeout,
            total_timeout=timeout + 1,
            max_retries=1,
            bearer_token=token,
        )
        for name, (url, timeout) in spec.items()
    }
    u = urlsplit(s.jwks_url)
    clients["oidc"] = ResilientClient(
        service_name=s.service_name,
        dependency="oidc",
        base_url=f"{u.scheme}://{u.netloc}",
        connect_timeout=2,
        read_timeout=4,
        total_timeout=5,
    )
    return clients


def build(settings: Settings | None = None, *, clients: dict[str, ResilientClient] | None = None) -> FastAPI:
    s = settings or get_settings()
    health = HealthRegistry(s.service_name, VERSION)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = None
        factory = None
        redis = None
        if s.app_env != "test":
            dsn = build_dsn(
                host=s.postgres_host,
                port=s.postgres_port,
                db=s.postgres_db,
                user=s.db_user,
                password=s.postgres_api_password.get_secret_value(),
            )
            engine = create_engine(dsn, schema="identity,travel")
            factory = session_factory(engine)
            redis = make_redis(s.redis_url)
            health.add("postgres", _pinger(engine), critical=True)

            async def redis_ok() -> None:
                await redis.ping()

            health.add("redis", redis_ok, critical=True)
        app.state.clients = clients or build_clients(s)
        c = app.state.clients
        jwks_path = urlsplit(s.jwks_url).path
        app.state.verifier = TokenVerifier(
            JWKSCache(c["oidc"], jwks_path, s.oidc_jwks_ttl_seconds),
            issuers=s.accepted_issuers,
            audience=s.oidc_audience,
            required_role=s.oidc_required_role,
            leeway_seconds=s.oidc_clock_skew_seconds,
        )

        async def oidc_ok() -> None:
            resp = await c["oidc"].request("GET", jwks_path, deadline_seconds=3)
            if resp.status_code != 200:
                raise RuntimeError("jwks unavailable")

        async def agent_ok() -> None:
            resp = await c["agent"].request("GET", "/health/live", deadline_seconds=2)
            if resp.status_code != 200:
                raise RuntimeError("agent down")

        health.add("oidc", oidc_ok, critical=True)
        health.add("agent", agent_ok, critical=False)
        app.state.repo = Repository(factory)
        app.state.cipher = ProfileCipher(
            s.emergency_profile_encryption_key.get_secret_value(), s.emergency_profile_key_version
        )
        app.state.limiter = RateLimiter(redis, s.app_env, s.trusted_proxy_count)
        app.state.agent = AgentClient(c["agent"], s.agent_submit_timeout_seconds, s.agent_poll_timeout_seconds)
        app.state.external = ExternalDataClient(c["external-data"], s.external_data_timeout_seconds)
        app.state.recommendation = RecommendationClient(c["recommendation"], s.recommendation_timeout_seconds)
        app.state.assessments = AssessmentService(s, app.state.repo, app.state.agent, redis)
        app.state.events = EventBridge(s, redis, app.state.agent)
        app.state.sse_guard = ConnectionGuard(s.max_sse_connections_per_user)
        app.state.alerts = ReassessmentConsumer(
            s, redis, app.state.repo, app.state.assessments, app.state.recommendation
        )
        await app.state.alerts.start()
        log.info("api_started", issuers=len(s.accepted_issuers), cors_origins=len(s.cors_allowed_origins))
        try:
            yield
        finally:
            await app.state.alerts.stop()
            for client in app.state.clients.values():
                await client.aclose()
            if redis is not None:
                await redis.aclose()
            if engine is not None:
                await engine.dispose()

    app = create_app(
        s,
        title="Smart Travel Assistant — Public API",
        version=VERSION,
        health=health,
        lifespan=lifespan,
        max_body_bytes=256_000,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_allowed_origins,  # exact origins only
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "If-Match", "Idempotency-Key", "Last-Event-ID", "X-Request-ID"],
        expose_headers=["ETag", "X-Request-ID", "X-Correlation-ID", "Retry-After"],
        max_age=600,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    for mod in (me, trips, runs, facades):
        app.include_router(mod.build_router(s))
    return app


def openapi_document(app: FastAPI) -> dict[str, Any]:
    doc = app.openapi()
    doc["servers"] = [{"url": "http://localhost:8000", "description": "local compose"}]
    doc.setdefault("components", {}).setdefault("securitySchemes", {})["oidc"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "Keycloak realm smart-travel, audience smart-travel-api, role traveler",
    }
    doc["security"] = [{"oidc": []}]
    return doc


app = build()
