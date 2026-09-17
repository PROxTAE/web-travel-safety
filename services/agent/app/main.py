"""agent service entry point (คน 3)."""

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
from sta_common.http import ResilientClient
from sta_common.internal_auth import InternalAuth
from sta_common.logging import get_logger

from app.api.internal import build_router
from app.graph.runner import RunManager
from app.progress.publisher import ProgressPublisher
from app.repositories.repo import RunRepository
from app.settings import Settings, get_settings

log = get_logger("agent")
VERSION = "1.0.0"


def _pinger(engine: AsyncEngine) -> Callable[[], Awaitable[None]]:
    async def check() -> None:
        await ping(engine)

    return check


def build_clients(s: Settings) -> dict[str, ResilientClient]:
    token = s.service_auth_token.get_secret_value() or None
    urls = {
        "external-data": s.external_data_service_url,
        "data-integration": s.data_integration_service_url,
        "risk-knowledge": s.risk_knowledge_service_url,
        "decision-engine": s.decision_service_url,
        "recommendation": s.recommendation_service_url,
    }
    return {
        name: ResilientClient(
            service_name=s.service_name,
            dependency=name,
            base_url=url,
            connect_timeout=3,
            read_timeout=s.agent_tool_timeout_seconds,
            total_timeout=s.agent_tool_timeout_seconds + 2,
            max_retries=1,
            bearer_token=token,
        )
        for name, url in urls.items()
    }


def build(settings: Settings | None = None, *, clients: dict[str, ResilientClient] | None = None) -> FastAPI:
    s = settings or get_settings()
    health = HealthRegistry(s.service_name, VERSION)
    auth = InternalAuth(s.service_auth_token.get_secret_value(), s.app_env)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = None
        factory = None
        redis = None
        checkpointer: Any = None
        cp_ctx: Any = None
        if s.app_env != "test":
            dsn = build_dsn(
                host=s.postgres_host,
                port=s.postgres_port,
                db=s.postgres_db,
                user=s.db_user,
                password=s.postgres_agent_password.get_secret_value(),
            )
            engine = create_engine(dsn, schema="agent")
            factory = session_factory(engine)
            redis = make_redis(s.redis_url)
            health.add("postgres", _pinger(engine), critical=True)

            async def redis_ok() -> None:
                await redis.ping()

            health.add("redis", redis_ok, critical=True)
            try:
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

                cp_ctx = AsyncPostgresSaver.from_conn_string(s.db_dsn_psycopg + "?options=-csearch_path%3Dagent")
                checkpointer = await cp_ctx.__aenter__()
                await checkpointer.setup()
            except Exception as exc:  # noqa: BLE001 - run without checkpoints rather than crash-loop; readiness shows it
                log.warning("checkpointer_unavailable", error_type=type(exc).__name__)
                checkpointer = None
        else:
            from langgraph.checkpoint.memory import MemorySaver

            checkpointer = MemorySaver()

        async def checkpointer_ok() -> None:
            if checkpointer is None:
                raise RuntimeError("no checkpointer")

        health.add("checkpointer", checkpointer_ok, critical=False)
        app.state.progress = ProgressPublisher(redis, s.app_env, s.run_state_ttl_seconds)
        app.state.repo = RunRepository(factory)
        app.state.clients = clients or build_clients(s)
        app.state.runs = RunManager(s, app.state.progress, app.state.repo, app.state.clients, checkpointer)
        log.info(
            "agent_started",
            graph_version=s.graph_version,
            budgets={"steps": s.max_agent_steps, "tools": s.max_tool_calls, "timeout_s": s.agent_total_timeout_seconds},
        )
        try:
            yield
        finally:
            for c in app.state.clients.values():
                await c.aclose()
            if cp_ctx is not None:
                await cp_ctx.__aexit__(None, None, None)
            if redis is not None:
                await redis.aclose()
            if engine is not None:
                await engine.dispose()

    app = create_app(
        s, title="Smart Travel — Agent", version=VERSION, health=health, lifespan=lifespan, max_body_bytes=8_000_000
    )
    app.include_router(build_router(s, auth))
    return app


app = build()
