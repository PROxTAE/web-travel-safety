"""Resilient outbound HTTP client.

- connect/read/total timeouts
- retries only on 408/429/temporary 5xx, honouring Retry-After, exponential backoff + jitter
- overall deadline propagation
- request/correlation/trace/contract headers
- stable error mapping (dependency timeout / unavailable / rate limited)
- no retry for non-idempotent calls unless an Idempotency-Key is present
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx

from sta_common.context import get_context
from sta_common.errors import AppError, ErrorCode
from sta_common.logging import get_logger
from sta_common.metrics import DEPENDENCY_DURATION, DEPENDENCY_ERRORS, DEPENDENCY_RETRIES, DEPENDENCY_TIMEOUTS

log = get_logger("http.client")

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class ResilientClient:
    def __init__(
        self,
        *,
        service_name: str,
        dependency: str,
        base_url: str,
        connect_timeout: float = 3.0,
        read_timeout: float = 10.0,
        total_timeout: float = 12.0,
        max_retries: int = 2,
        bearer_token: str | None = None,
        default_headers: dict[str, str] | None = None,
        max_concurrency: int = 16,
    ) -> None:
        self.service_name = service_name
        self.dependency = dependency
        self.base_url = base_url.rstrip("/")
        self.total_timeout = total_timeout
        self.max_retries = max_retries
        headers = {"Accept": "application/json", "User-Agent": f"smart-travel-assistant/{service_name}"}
        if default_headers:
            headers.update(default_headers)
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=httpx.Timeout(connect=connect_timeout, read=read_timeout, write=read_timeout, pool=connect_timeout),
            limits=httpx.Limits(max_connections=max_concurrency * 2, max_keepalive_connections=max_concurrency),
            follow_redirects=False,
        )
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        deadline_seconds: float | None = None,
        idempotent: bool | None = None,
    ) -> httpx.Response:
        deadline = time.monotonic() + (deadline_seconds if deadline_seconds is not None else self.total_timeout)
        ctx = get_context()
        req_headers = ctx.outbound_headers()
        if headers:
            req_headers.update(headers)
        if idempotent is None:
            idempotent = method.upper() in {"GET", "HEAD", "OPTIONS"} or "Idempotency-Key" in req_headers
        attempt = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                DEPENDENCY_TIMEOUTS.labels(self.service_name, self.dependency).inc()
                raise AppError(ErrorCode.DEPENDENCY_TIMEOUT, f"{self.dependency} deadline exceeded")
            started = time.perf_counter()
            try:
                async with self._semaphore:
                    resp = await asyncio.wait_for(
                        self._client.request(
                            method, path, params=params, json=json, content=content, headers=req_headers
                        ),
                        timeout=remaining,
                    )
            except (TimeoutError, httpx.TimeoutException):
                DEPENDENCY_TIMEOUTS.labels(self.service_name, self.dependency).inc()
                DEPENDENCY_DURATION.labels(self.service_name, self.dependency, "timeout").observe(
                    time.perf_counter() - started
                )
                if idempotent and attempt < self.max_retries:
                    attempt += 1
                    DEPENDENCY_RETRIES.labels(self.service_name, self.dependency).inc()
                    await self._sleep(attempt, None, deadline)
                    continue
                raise AppError(ErrorCode.DEPENDENCY_TIMEOUT, f"{self.dependency} timed out") from None
            except httpx.HTTPError as exc:
                DEPENDENCY_ERRORS.labels(self.service_name, self.dependency, "connect").inc()
                DEPENDENCY_DURATION.labels(self.service_name, self.dependency, "error").observe(
                    time.perf_counter() - started
                )
                if idempotent and attempt < self.max_retries:
                    attempt += 1
                    DEPENDENCY_RETRIES.labels(self.service_name, self.dependency).inc()
                    await self._sleep(attempt, None, deadline)
                    continue
                log.warning("dependency_unreachable", dependency=self.dependency, error_type=type(exc).__name__)
                raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, f"{self.dependency} unreachable") from None

            DEPENDENCY_DURATION.labels(self.service_name, self.dependency, str(resp.status_code // 100) + "xx").observe(
                time.perf_counter() - started
            )
            if resp.status_code in RETRYABLE_STATUS and idempotent and attempt < self.max_retries:
                attempt += 1
                DEPENDENCY_RETRIES.labels(self.service_name, self.dependency).inc()
                await self._sleep(attempt, resp.headers.get("Retry-After"), deadline)
                continue
            return resp

    async def _sleep(self, attempt: int, retry_after: str | None, deadline: float) -> None:
        delay = 0.25 * (2 ** (attempt - 1)) + random.uniform(0, 0.2)  # noqa: S311 - jitter, not crypto
        if retry_after and retry_after.isdigit():
            delay = max(delay, float(retry_after))
        remaining = deadline - time.monotonic()
        if delay >= remaining:
            DEPENDENCY_TIMEOUTS.labels(self.service_name, self.dependency).inc()
            raise AppError(ErrorCode.DEPENDENCY_TIMEOUT, f"{self.dependency} retry would exceed deadline")
        await asyncio.sleep(delay)

    async def get_json(self, path: str, **kw: Any) -> Any:
        resp = await self.request("GET", path, **kw)
        return self.raise_for_envelope(resp)

    async def post_json(self, path: str, json: Any, **kw: Any) -> Any:
        resp = await self.request("POST", path, json=json, **kw)
        return self.raise_for_envelope(resp)

    def raise_for_envelope(self, resp: httpx.Response) -> Any:
        """Map an internal-service response to data or a stable AppError."""
        if 200 <= resp.status_code < 300:
            if resp.status_code == 204 or not resp.content:
                return None
            return resp.json()
        code = ErrorCode.DEPENDENCY_UNAVAILABLE
        message = f"{self.dependency} returned {resp.status_code}"
        retry_after = None
        try:
            payload = resp.json()
            err = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(err, dict):
                raw = err.get("code")
                if isinstance(raw, str) and raw in ErrorCode.__members__:
                    code = ErrorCode(raw)
                message = str(err.get("message") or message)
                retry_after = err.get("retry_after_seconds")
        except ValueError:
            pass
        if resp.status_code == 429:
            code = ErrorCode.RATE_LIMITED
        elif resp.status_code in (408, 504):
            code = ErrorCode.DEPENDENCY_TIMEOUT
        DEPENDENCY_ERRORS.labels(self.service_name, self.dependency, code).inc()
        raise AppError(code, message, retry_after_seconds=retry_after, details={"upstream_status": resp.status_code})
