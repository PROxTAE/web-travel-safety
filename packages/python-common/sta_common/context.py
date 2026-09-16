"""Per-request context propagated through contextvars (request_id, correlation_id, trace)."""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field

REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"
CONTRACT_VERSION_HEADER = "X-Contract-Version"
TRACEPARENT_HEADER = "traceparent"


@dataclass(slots=True)
class RequestContext:
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    traceparent: str | None = None
    contract_version: str = "1.0.0"
    # Set by auth layers; never logged.
    subject: str | None = None

    def outbound_headers(self) -> dict[str, str]:
        headers = {
            REQUEST_ID_HEADER: self.request_id,
            CORRELATION_ID_HEADER: self.correlation_id,
            CONTRACT_VERSION_HEADER: self.contract_version,
        }
        if self.traceparent:
            headers[TRACEPARENT_HEADER] = self.traceparent
        return headers


_current: ContextVar[RequestContext | None] = ContextVar("sta_request_context", default=None)


def get_context() -> RequestContext:
    ctx = _current.get()
    if ctx is None:
        ctx = RequestContext()
        _current.set(ctx)
    return ctx


def set_context(ctx: RequestContext) -> None:
    _current.set(ctx)


def reset_context() -> None:
    _current.set(None)


def new_uuid() -> str:
    return str(uuid.uuid4())
