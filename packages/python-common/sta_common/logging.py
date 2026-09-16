"""structlog JSON logging with mandatory fields and PII redaction.

Fields (00_SHARED_PROJECT_CONTEXT §12): timestamp, level, service, environment,
request_id, correlation_id, trace_id, event, duration_ms, status, error_code.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog
from opentelemetry import trace

from sta_common.context import _current
from sta_common.redaction import redact


def _add_service_fields(service: str, environment: str):  # type: ignore[no-untyped-def]
    def processor(_logger: Any, _method: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
        event_dict.setdefault("service", service)
        event_dict.setdefault("environment", environment)
        ctx = _current.get()
        if ctx is not None:
            event_dict.setdefault("request_id", ctx.request_id)
            event_dict.setdefault("correlation_id", ctx.correlation_id)
        span = trace.get_current_span()
        sc = span.get_span_context() if span else None
        if sc is not None and sc.is_valid:
            event_dict.setdefault("trace_id", format(sc.trace_id, "032x"))
        return event_dict

    return processor


def _redact_processor(_logger: Any, _method: str, event_dict: MutableMapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = redact(dict(event_dict))
    return out


def configure_logging(service: str, environment: str, level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
            _add_service_fields(service, environment),
            _redact_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=False,
    )
    # Quiet noisy libraries; uvicorn access logs are replaced by our middleware.
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
