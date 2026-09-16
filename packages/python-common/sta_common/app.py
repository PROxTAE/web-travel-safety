"""FastAPI application factory used by every service."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager

from fastapi import FastAPI
from fastapi.responses import ORJSONResponse

from sta_common.errors import install_error_handlers
from sta_common.health import HealthRegistry
from sta_common.logging import configure_logging
from sta_common.metrics import metrics_endpoint
from sta_common.middleware import RequestContextMiddleware
from sta_common.settings import BaseServiceSettings
from sta_common.tracing import configure_tracing

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]] | Callable[[FastAPI], AsyncIterator[None]]


def create_app(
    settings: BaseServiceSettings,
    *,
    title: str,
    version: str,
    health: HealthRegistry,
    lifespan: Lifespan | None = None,
    max_body_bytes: int = 1_000_000,
) -> FastAPI:
    configure_logging(settings.service_name, settings.app_env, settings.log_level)
    app = FastAPI(
        title=title,
        version=version,
        default_response_class=ORJSONResponse,
        lifespan=lifespan,  # type: ignore[arg-type]
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
        openapi_url="/openapi.json" if not settings.is_production else None,
    )
    app.add_middleware(
        RequestContextMiddleware,
        service_name=settings.service_name,
        contract_version=settings.contract_version,
        max_body_bytes=max_body_bytes,
    )
    install_error_handlers(app, settings.contract_version)
    app.include_router(health.router())
    app.add_route("/metrics", metrics_endpoint, methods=["GET"])
    configure_tracing(
        app,
        settings.service_name,
        settings.app_env,
        settings.otel_exporter_otlp_endpoint,
        settings.otel_traces_enabled,
    )
    app.state.settings = settings
    app.state.health = health
    return app
