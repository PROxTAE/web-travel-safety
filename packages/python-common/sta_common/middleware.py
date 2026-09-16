"""Request context + access logging + metrics + body-size middleware."""

from __future__ import annotations

import re
import time

from fastapi.responses import ORJSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from sta_common.context import (
    CONTRACT_VERSION_HEADER,
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
    TRACEPARENT_HEADER,
    RequestContext,
    reset_context,
    set_context,
)
from sta_common.logging import get_logger
from sta_common.metrics import HTTP_DURATION, HTTP_REQUESTS

log = get_logger("http")
_UUID_LIKE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def _safe_id(value: str | None) -> str | None:
    if value and _UUID_LIKE.match(value):
        return value
    return None


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, service_name: str, contract_version: str, max_body_bytes: int = 1_000_000):  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.service_name = service_name
        self.contract_version = contract_version
        self.max_body_bytes = max_body_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        ctx = RequestContext(
            request_id=_safe_id(request.headers.get(REQUEST_ID_HEADER)) or RequestContext().request_id,
            correlation_id=_safe_id(request.headers.get(CORRELATION_ID_HEADER)) or RequestContext().correlation_id,
            traceparent=request.headers.get(TRACEPARENT_HEADER),
            contract_version=request.headers.get(CONTRACT_VERSION_HEADER) or self.contract_version,
        )
        set_context(ctx)
        started = time.perf_counter()
        route = request.scope.get("path", "")
        status = 500
        try:
            content_length = request.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > self.max_body_bytes:
                status = 413
                return ORJSONResponse(
                    {
                        "error": {"code": "PAYLOAD_TOO_LARGE", "message": "Request body too large", "retryable": False},
                        "meta": {"request_id": ctx.request_id, "correlation_id": ctx.correlation_id},
                    },
                    status_code=413,
                )
            response = await call_next(request)
            status = response.status_code
            response.headers[REQUEST_ID_HEADER] = ctx.request_id
            response.headers[CORRELATION_ID_HEADER] = ctx.correlation_id
            return response
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            template = _route_template(request) or route
            if template not in ("/health/live", "/health/ready", "/metrics"):
                log.info(
                    "http_request",
                    method=request.method,
                    route=template,
                    status=status,
                    duration_ms=round(duration_ms, 2),
                )
            HTTP_REQUESTS.labels(self.service_name, request.method, template, str(status)).inc()
            HTTP_DURATION.labels(self.service_name, request.method, template).observe(duration_ms / 1000)
            reset_context()


def _route_template(request: Request) -> str | None:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else None
