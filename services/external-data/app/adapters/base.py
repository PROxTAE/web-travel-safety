"""Common adapter contract (04 plan §"Common adapter interface") and provider error codes."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import httpx
from sta_common.errors import AppError, ErrorCode
from sta_common.http import ResilientClient
from sta_common.logging import get_logger
from sta_common.metrics import DEPENDENCY_ERRORS
from sta_contracts.enums import ProviderKind
from sta_contracts.models import ProviderHealth

log = get_logger("adapter")


class ProviderErrorCode(StrEnum):
    PROVIDER_AUTH = "PROVIDER_AUTH"
    PROVIDER_QUOTA = "PROVIDER_QUOTA"
    PROVIDER_RATE_LIMIT = "PROVIDER_RATE_LIMIT"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_SCHEMA_CHANGED = "PROVIDER_SCHEMA_CHANGED"
    PROVIDER_OUTAGE = "PROVIDER_OUTAGE"
    OUTSIDE_COVERAGE = "OUTSIDE_COVERAGE"
    LICENSE_RESTRICTION = "LICENSE_RESTRICTION"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class ProviderError(Exception):
    def __init__(
        self, code: ProviderErrorCode, message: str, *, retryable: bool = False, retry_after: int | None = None
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after

    def to_app_error(self) -> AppError:
        mapping = {
            ProviderErrorCode.PROVIDER_AUTH: ErrorCode.DEPENDENCY_UNAVAILABLE,
            ProviderErrorCode.PROVIDER_QUOTA: ErrorCode.DEPENDENCY_UNAVAILABLE,
            ProviderErrorCode.PROVIDER_RATE_LIMIT: ErrorCode.RATE_LIMITED,
            ProviderErrorCode.PROVIDER_TIMEOUT: ErrorCode.DEPENDENCY_TIMEOUT,
            ProviderErrorCode.PROVIDER_SCHEMA_CHANGED: ErrorCode.DEPENDENCY_UNAVAILABLE,
            ProviderErrorCode.PROVIDER_OUTAGE: ErrorCode.DEPENDENCY_UNAVAILABLE,
            ProviderErrorCode.OUTSIDE_COVERAGE: ErrorCode.UNSUPPORTED_COVERAGE,
            ProviderErrorCode.LICENSE_RESTRICTION: ErrorCode.UNSUPPORTED_COVERAGE,
            ProviderErrorCode.NOT_CONFIGURED: ErrorCode.UNSUPPORTED_COVERAGE,
        }
        return AppError(mapping[self.code], f"{self.message}", retry_after_seconds=self.retry_after)


@dataclass(slots=True)
class Coverage:
    supported: bool
    reason: str | None = None


@dataclass(slots=True)
class ProviderDescriptor:
    name: str
    kind: ProviderKind
    version: str
    authority: str
    license: str
    attribution: str
    docs_url: str
    enabled: bool = True
    coverage_note: str | None = None


@dataclass
class HealthState:
    """In-process health/circuit state per provider (persisted periodically by the health repository)."""

    status: str = "UNKNOWN"
    consecutive_failures: int = 0
    opened_at: float | None = None
    last_latency_ms: float | None = None
    last_error_code: str | None = None
    quota_remaining: int | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class CircuitOpenError(ProviderError):
    def __init__(self, provider: str, retry_after: int) -> None:
        super().__init__(
            ProviderErrorCode.PROVIDER_OUTAGE, f"{provider} circuit open", retryable=True, retry_after=retry_after
        )


class BaseAdapter:
    descriptor: ProviderDescriptor

    def __init__(self, *, service_name: str, failure_threshold: int = 5, open_seconds: int = 60) -> None:
        self.service_name = service_name
        self.health = HealthState()
        self.failure_threshold = failure_threshold
        self.open_seconds = open_seconds

    # ---- circuit breaker -------------------------------------------------
    def _check_circuit(self) -> None:
        if self.health.opened_at is None:
            return
        elapsed = time.monotonic() - self.health.opened_at
        if elapsed < self.open_seconds:
            raise CircuitOpenError(self.descriptor.name, int(self.open_seconds - elapsed) + 1)
        # half-open: allow one probe
        self.health.opened_at = None
        self.health.status = "DEGRADED"

    def _record_success(self, latency_ms: float, quota_remaining: int | None = None) -> None:
        self.health.consecutive_failures = 0
        self.health.opened_at = None
        self.health.status = "UP"
        self.health.last_latency_ms = latency_ms
        self.health.last_error_code = None
        if quota_remaining is not None:
            self.health.quota_remaining = quota_remaining
        self.health.checked_at = datetime.now(UTC)

    def _record_failure(self, code: ProviderErrorCode) -> None:
        self.health.consecutive_failures += 1
        self.health.last_error_code = code.value
        self.health.checked_at = datetime.now(UTC)
        DEPENDENCY_ERRORS.labels(self.service_name, self.descriptor.name, code.value).inc()
        if code in (ProviderErrorCode.OUTSIDE_COVERAGE, ProviderErrorCode.NOT_CONFIGURED):
            return
        if self.health.consecutive_failures >= self.failure_threshold:
            self.health.opened_at = time.monotonic()
            self.health.status = "DOWN"
            log.warning("circuit_opened", provider=self.descriptor.name, error_code=code.value)
        else:
            self.health.status = "DEGRADED"

    def provider_health(self) -> ProviderHealth:
        status = self.health.status if self.descriptor.enabled else "UNAVAILABLE"
        return ProviderHealth(
            provider=self.descriptor.name,
            kind=self.descriptor.kind.value,
            status=status,
            enabled=self.descriptor.enabled,
            latency_ms=self.health.last_latency_ms,
            quota_remaining=self.health.quota_remaining,
            coverage_note=self.descriptor.coverage_note,
            checked_at=self.health.checked_at,
            last_error_code=self.health.last_error_code,
        )

    # ---- shared HTTP handling -------------------------------------------
    async def _call(
        self, client: ResilientClient, method: str, path: str, *, deadline: float | None = None, **kw: Any
    ) -> httpx.Response:
        self._check_circuit()
        started = time.perf_counter()
        try:
            resp = await client.request(method, path, deadline_seconds=deadline, **kw)
        except AppError as exc:
            code = (
                ProviderErrorCode.PROVIDER_TIMEOUT
                if exc.code == ErrorCode.DEPENDENCY_TIMEOUT
                else ProviderErrorCode.PROVIDER_OUTAGE
            )
            self._record_failure(code)
            raise ProviderError(code, f"{self.descriptor.name}: {exc.message}", retryable=True) from None
        latency = (time.perf_counter() - started) * 1000
        if resp.status_code in (401, 403):
            self._record_failure(ProviderErrorCode.PROVIDER_AUTH)
            raise ProviderError(ProviderErrorCode.PROVIDER_AUTH, f"{self.descriptor.name} rejected credentials")
        if resp.status_code == 429:
            self._record_failure(ProviderErrorCode.PROVIDER_RATE_LIMIT)
            ra = resp.headers.get("Retry-After")
            raise ProviderError(
                ProviderErrorCode.PROVIDER_RATE_LIMIT,
                f"{self.descriptor.name} rate limited",
                retryable=True,
                retry_after=int(ra) if ra and ra.isdigit() else None,
            )
        if resp.status_code >= 500:
            self._record_failure(ProviderErrorCode.PROVIDER_OUTAGE)
            raise ProviderError(
                ProviderErrorCode.PROVIDER_OUTAGE, f"{self.descriptor.name} returned {resp.status_code}", retryable=True
            )
        if resp.status_code >= 400:
            self._record_failure(ProviderErrorCode.PROVIDER_SCHEMA_CHANGED)
            raise ProviderError(
                ProviderErrorCode.PROVIDER_SCHEMA_CHANGED,
                f"{self.descriptor.name} rejected request ({resp.status_code})",
            )
        quota = resp.headers.get("x-ratelimit-remaining")
        self._record_success(latency, int(quota) if quota and quota.isdigit() else None)
        return resp

    @staticmethod
    def _json(resp: httpx.Response, provider: str) -> Any:
        try:
            return resp.json()
        except ValueError:
            raise ProviderError(
                ProviderErrorCode.PROVIDER_SCHEMA_CHANGED, f"{provider} returned non-JSON body"
            ) from None
