"""Prometheus metrics shared by all services (names are stable dashboard contracts)."""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response

HTTP_REQUESTS = Counter("sta_http_requests_total", "HTTP requests", ["service", "method", "route", "status"])
HTTP_DURATION = Histogram(
    "sta_http_request_duration_seconds",
    "HTTP request latency",
    ["service", "method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
DEPENDENCY_DURATION = Histogram(
    "sta_dependency_duration_seconds",
    "Outbound dependency latency",
    ["service", "dependency", "outcome"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20),
)
DEPENDENCY_ERRORS = Counter(
    "sta_dependency_errors_total", "Outbound dependency errors", ["service", "dependency", "error_code"]
)
DEPENDENCY_RETRIES = Counter("sta_dependency_retries_total", "Outbound retries", ["service", "dependency"])
DEPENDENCY_TIMEOUTS = Counter("sta_dependency_timeouts_total", "Outbound timeouts", ["service", "dependency"])
CACHE_EVENTS = Counter("sta_cache_events_total", "Cache hit/miss/stale", ["service", "cache", "outcome"])
DEGRADED_RESULTS = Counter("sta_degraded_results_total", "Results flagged degraded", ["service", "reason"])
READINESS = Gauge("sta_readiness", "1 when ready", ["service"])
DECISION_ACTIONS = Counter("sta_decision_actions_total", "Locked actions", ["action"])
LLM_CALLS = Counter("sta_llm_calls_total", "LLM calls", ["service", "outcome"])
LLM_FALLBACK = Counter("sta_llm_fallback_total", "Fixed-template fallbacks", ["service", "reason"])


async def metrics_endpoint(_: Request) -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
