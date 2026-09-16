"""decision-engine service entry point (คน 7)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine
from sta_common.app import create_app
from sta_common.db import build_dsn, create_engine, ping, session_factory
from sta_common.health import HealthRegistry
from sta_common.internal_auth import InternalAuth
from sta_common.logging import get_logger

from app.api.internal import build_router
from app.llm.client import ExplanationClient
from app.policy.loader import load_policy
from app.repositories.models import AuditRepository
from app.settings import Settings, get_settings

log = get_logger("decision-engine")
VERSION = "1.0.0"


def _pinger(engine: AsyncEngine) -> Callable[[], Awaitable[None]]:
    async def check() -> None:
        await ping(engine)

    return check


def build(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    health = HealthRegistry(s.service_name, VERSION)
    auth = InternalAuth(s.service_auth_token.get_secret_value(), s.app_env)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # readiness fails when no approved policy loads (plan Phase 1 step 4)
        app.state.policy = load_policy(s.policy_path, s.policy_schema_path)
        app.state.llm = ExplanationClient(
            api_key=s.openai_api_key.get_secret_value(),
            model=s.openai_explainer_model,
            prompts_dir=s.prompts_dir,
            service_name=s.service_name,
            timeout_seconds=s.openai_timeout_seconds,
            max_output_tokens=s.openai_max_output_tokens,
            enabled=s.llm_enabled,
        )
        engine = None
        factory = None
        if s.app_env != "test":
            dsn = build_dsn(
                host=s.postgres_host,
                port=s.postgres_port,
                db=s.postgres_db,
                user=s.db_user,
                password=s.postgres_decision_password.get_secret_value(),
            )
            engine = create_engine(dsn, schema="decision")
            factory = session_factory(engine)
            health.add("postgres", _pinger(engine), critical=True)
        app.state.audit = AuditRepository(factory)

        async def policy_ok() -> None:
            if app.state.policy.status != "APPROVED":
                raise RuntimeError("policy not approved")

        health.add("policy", policy_ok, critical=True)
        log.info(
            "decision_engine_started",
            policy_version=app.state.policy.version,
            policy_checksum=app.state.policy.checksum[:12],
            llm_enabled=app.state.llm.enabled,
        )
        try:
            yield
        finally:
            if engine is not None:
                await engine.dispose()

    app = create_app(
        s,
        title="Smart Travel — Decision Engine",
        version=VERSION,
        health=health,
        lifespan=lifespan,
        max_body_bytes=8_000_000,
    )
    app.include_router(build_router(s, auth))
    return app


app = build()
