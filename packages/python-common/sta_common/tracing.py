"""OpenTelemetry setup. Exporting is optional; instrumentation is always on so trace IDs exist in logs."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased


def configure_tracing(app: FastAPI, service_name: str, environment: str, endpoint: str | None, enabled: bool) -> None:
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name, "deployment.environment": environment}),
        sampler=ParentBased(TraceIdRatioBased(1.0 if environment != "production" else 0.2)),
    )
    if enabled and endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces"))
            )
        except Exception as exc:  # noqa: BLE001 - tracing must never break startup
            logging.getLogger(__name__).warning("otlp_exporter_unavailable error_type=%s", type(exc).__name__)
    trace.set_tracer_provider(provider)
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="health/live,health/ready,metrics")
        HTTPXClientInstrumentor().instrument()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("otel_instrumentation_unavailable error_type=%s", type(exc).__name__)
