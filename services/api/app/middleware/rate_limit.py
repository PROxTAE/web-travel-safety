"""Fixed-window rate limiter keyed ``sta:{env}:rate:{subject}:{endpoint_class}`` (Redis INCR+EXPIRE, memory fallback).

Subject = user id for authenticated calls, else the client IP behind ``TRUSTED_PROXY_COUNT`` proxies. Limits return
429 with ``Retry-After`` and never leak the subject.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from fastapi import Depends, Request
from redis.asyncio import Redis
from sta_common.cache import key as redis_key
from sta_common.errors import AppError, ErrorCode
from sta_common.logging import get_logger

from app.auth.deps import CurrentUser, current_user

log = get_logger("rate-limit")
WINDOW_SECONDS = 60


class RateLimiter:
    def __init__(self, redis: Redis | None, env: str, trusted_proxies: int) -> None:
        self.redis = redis
        self.env = env
        self.trusted_proxies = trusted_proxies
        self._mem: dict[str, tuple[int, float]] = {}

    def client_ip(self, request: Request) -> str:
        if self.trusted_proxies > 0:
            xff = [p.strip() for p in request.headers.get("X-Forwarded-For", "").split(",") if p.strip()]
            if len(xff) >= self.trusted_proxies:
                return xff[-self.trusted_proxies]
        return request.client.host if request.client else "unknown"

    async def hit(self, subject: str, endpoint_class: str, limit: int) -> None:
        k = redis_key(self.env, "rate", subject, endpoint_class)
        now = time.time()
        if self.redis is not None:
            try:
                count = int(await self.redis.incr(k))
                if count == 1:
                    await self.redis.expire(k, WINDOW_SECONDS)
                ttl = int(await self.redis.ttl(k)) if count > limit else 0
            except Exception as exc:  # noqa: BLE001 - fail open on cache outage but say so in logs/metrics
                log.warning("rate_limit_backend_unavailable", error_type=type(exc).__name__)
                return
        else:
            count, reset = self._mem.get(k, (0, now + WINDOW_SECONDS))
            if now > reset:
                count, reset = 0, now + WINDOW_SECONDS
            count += 1
            self._mem[k] = (count, reset)
            ttl = max(1, int(reset - now))
        if count > limit:
            raise AppError(ErrorCode.RATE_LIMITED, "rate limit exceeded", retry_after_seconds=max(1, ttl))


def user_limit(endpoint_class: str, limit_attr: str) -> Callable[..., Any]:
    async def dep(request: Request, user: CurrentUser = Depends(current_user)) -> CurrentUser:
        limiter: RateLimiter = request.app.state.limiter
        await limiter.hit(f"u_{user.id}", endpoint_class, getattr(request.app.state.settings, limit_attr))
        return user

    return dep


def ip_limit(endpoint_class: str, limit_attr: str) -> Callable[..., Any]:
    async def dep(request: Request) -> None:
        limiter: RateLimiter = request.app.state.limiter
        await limiter.hit(
            f"ip_{limiter.client_ip(request)}", endpoint_class, getattr(request.app.state.settings, limit_attr)
        )

    return dep
