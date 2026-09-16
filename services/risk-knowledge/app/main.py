"""risk-knowledge service entry point (คน 6)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI
from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncEngine
from sta_common.app import create_app
from sta_common.db import build_dsn, create_engine, ping, session_factory
from sta_common.health import HealthRegistry
from sta_common.internal_auth import InternalAuth
from sta_common.logging import get_logger

from app.api.internal import build_router
from app.knowledge.embeddings import make_embedder
from app.knowledge.index import KnowledgeIndex
from app.risk.model_loader import ModelUnavailableError, load_active_model
from app.risk.thresholds import load_overrides, load_thresholds
from app.settings import Settings, get_settings

log = get_logger("risk-knowledge")
VERSION = "1.0.0"


def _pinger(engine: AsyncEngine) -> Callable[[], Awaitable[None]]:
    async def check() -> None:
        await ping(engine)

    return check


def feature_schema_version(path: str) -> str:
    p = Path(path)
    if not p.exists():
        # inside the container the schema is copied next to the app
        p = Path("config/feature_schema.yaml")
    return str(yaml.safe_load(p.read_text(encoding="utf-8"))["version"])


def build(settings: Settings | None = None, *, qdrant: QdrantClient | None = None) -> FastAPI:
    s = settings or get_settings()
    health = HealthRegistry(s.service_name, VERSION)
    auth = InternalAuth(s.service_auth_token.get_secret_value(), s.app_env)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.thresholds = load_thresholds(s.thresholds_path)
        app.state.overrides = load_overrides(s.overrides_path)
        app.state.feature_schema_version = feature_schema_version(s.feature_schema_path)
        try:
            app.state.model = load_active_model(
                s.risk_model_artifact_dir, expected_feature_schema=app.state.feature_schema_version
            )
            app.state.model_unavailable_reason = None
        except ModelUnavailableError as exc:
            app.state.model = None
            app.state.model_unavailable_reason = exc.reason
            log.warning("model_unavailable", reason=exc.reason)
        client = qdrant or (
            QdrantClient(location=":memory:") if s.app_env == "test" else QdrantClient(url=s.qdrant_url, timeout=10)
        )
        embedder = await asyncio.to_thread(make_embedder, s.embedding_provider, s.embedding_model_name, s.app_env)
        app.state.index = KnowledgeIndex(client, embedder, s.knowledge_collection_prefix)
        try:
            status = await asyncio.to_thread(app.state.index.load_active)
            log.info("knowledge_loaded", collection=status.collection_name, points=status.points, model=embedder.name)
        except Exception as exc:  # noqa: BLE001 - qdrant may be booting; readiness reflects it
            log.warning("knowledge_load_deferred", error_type=type(exc).__name__)
        engine = None
        if s.app_env != "test":
            dsn = build_dsn(
                host=s.postgres_host,
                port=s.postgres_port,
                db=s.postgres_db,
                user=s.db_user,
                password=s.postgres_knowledge_password.get_secret_value(),
            )
            engine = create_engine(dsn, schema="knowledge")
            app.state.db = session_factory(engine)
            health.add("postgres", _pinger(engine), critical=True)

            async def qdrant_ok() -> None:
                await asyncio.to_thread(client.get_collections)

            health.add("qdrant", qdrant_ok, critical=False)

        async def model_ok() -> None:
            if app.state.model is None:
                raise RuntimeError("rule-baseline")

        health.add("risk_model", model_ok, critical=False)
        try:
            yield
        finally:
            if engine is not None:
                await engine.dispose()

    app = create_app(
        s,
        title="Smart Travel — Risk & Knowledge",
        version=VERSION,
        health=health,
        lifespan=lifespan,
        max_body_bytes=8_000_000,
    )
    app.include_router(build_router(s, auth))
    return app


app = build()
