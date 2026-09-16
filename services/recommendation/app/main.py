"""recommendation service entry point (คน 8)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine
from sta_common.app import create_app
from sta_common.cache import make_redis
from sta_common.db import build_dsn, create_engine, ping, session_factory
from sta_common.health import HealthRegistry
from sta_common.internal_auth import InternalAuth
from sta_common.logging import get_logger

from app.api.internal import build_router
from app.directory.resolver import Directory
from app.domain.alert_service import AlertService
from app.notifications.channels import Channel, EmailChannel, InAppChannel, WebPushChannel
from app.repositories.repo import Repository
from app.settings import Settings, get_settings

log = get_logger("recommendation")
VERSION = "1.0.0"


def _pinger(engine: AsyncEngine) -> Callable[[], Awaitable[None]]:
    async def check() -> None:
        await ping(engine)

    return check


def build_channels(s: Settings, redis: Any) -> dict[str, Channel]:
    return {
        "IN_APP": InAppChannel(redis, s.app_env),
        "PUSH": WebPushChannel(s.vapid_private_key.get_secret_value(), s.vapid_subject),
        "EMAIL": EmailChannel(s.smtp_host, s.smtp_port, s.email_sender, s.email_enabled),
    }


def build(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    health = HealthRegistry(s.service_name, VERSION)
    auth = InternalAuth(s.service_auth_token.get_secret_value(), s.app_env)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.directory = Directory.load(s.emergency_directory_path)
        engine = None
        factory = None
        redis = None
        if s.app_env != "test":
            dsn = build_dsn(
                host=s.postgres_host,
                port=s.postgres_port,
                db=s.postgres_db,
                user=s.db_user,
                password=s.postgres_recommendation_password.get_secret_value(),
            )
            engine = create_engine(dsn, schema="recommendation")
            factory = session_factory(engine)
            redis = make_redis(s.redis_url)
            health.add("postgres", _pinger(engine), critical=True)

            async def redis_ok() -> None:
                await redis.ping()

            health.add("redis", redis_ok, critical=False)
        app.state.repo = Repository(factory)
        app.state.channels = build_channels(s, redis)
        app.state.alerts = AlertService(app.state.repo, app.state.channels)
        verified = sum(1 for r in app.state.directory.records if r.review_status == "VERIFIED")
        log.info(
            "recommendation_started",
            directory_version=app.state.directory.directory_version,
            verified_contacts=verified,
        )
        try:
            yield
        finally:
            if redis is not None:
                await redis.aclose()
            if engine is not None:
                await engine.dispose()

    app = create_app(
        s,
        title="Smart Travel — Recommendation",
        version=VERSION,
        health=health,
        lifespan=lifespan,
        max_body_bytes=8_000_000,
    )
    app.include_router(build_router(s, auth))
    return app


app = build()
