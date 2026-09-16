"""data-integration service entry point (คน 5)."""

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
from app.domain.features import load_schema
from app.repositories.snapshot_repo import SnapshotRepository
from app.settings import Settings, get_settings

log = get_logger("data-integration")
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
        engine = None
        factory = None
        if s.app_env != "test":
            dsn = build_dsn(
                host=s.postgres_host,
                port=s.postgres_port,
                db=s.postgres_db,
                user=s.db_user,
                password=s.postgres_integration_password.get_secret_value(),
            )
            engine = create_engine(dsn, schema="integration")
            factory = session_factory(engine)
            health.add("postgres", _pinger(engine), critical=True)
        app.state.repo = SnapshotRepository(factory)
        app.state.feature_schema = load_schema(s.feature_schema_path)
        log.info(
            "data_integration_started",
            feature_schema_version=app.state.feature_schema["version"],
            transform_version=s.transform_version,
        )
        try:
            yield
        finally:
            if engine is not None:
                await engine.dispose()

    app = create_app(
        s,
        title="Smart Travel — Data Integration",
        version=VERSION,
        health=health,
        lifespan=lifespan,
        max_body_bytes=8_000_000,
    )
    app.include_router(build_router(s, auth))
    return app


app = build()
