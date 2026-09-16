"""Redis provider cache with TTL, negative cache, stampede lock and explicit stale flagging.

Key: sta:{env}:provider-cache:{provider}:{schema}:{hash(query)}
Policy: stale-while-revalidate is only used by callers that pass ``allow_stale=True``; stale
records are returned with ``DataStatus.STALE`` + ``expires_at`` untouched so consumers can see it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import orjson
from pydantic import BaseModel
from sta_common.cache import Cache, stable_hash
from sta_common.logging import get_logger
from sta_common.metrics import CACHE_EVENTS

log = get_logger("provider-cache")
T = TypeVar("T", bound=BaseModel)


class ProviderCache:
    def __init__(self, cache: Cache | None, service_name: str, schema_version: str) -> None:
        self.cache = cache
        self.service_name = service_name
        self.schema_version = schema_version

    def _key(self, provider: str, query: Any) -> str:
        assert self.cache is not None
        return self.cache.k("provider-cache", provider, self.schema_version, stable_hash(query))

    async def get_or_fetch(
        self,
        *,
        provider: str,
        query: Any,
        ttl_seconds: int,
        model: type[T],
        fetch: Callable[[], Awaitable[list[T]]],
        negative_ttl: int = 30,
    ) -> tuple[list[T], bool]:
        """Return (records, from_cache). Records are validated through ``model`` when read back."""
        if self.cache is None:
            return await fetch(), False
        key = self._key(provider, query)
        try:
            cached = await self.cache.get_json(key)
        except Exception:  # noqa: BLE001 - cache outage must not block real fetch
            cached = None
        if cached is not None:
            CACHE_EVENTS.labels(self.service_name, provider, "hit").inc()
            if cached.get("negative"):
                raise NegativeCachedError(cached.get("error", "provider returned no data recently"))
            return [model.model_validate(r) for r in cached["records"]], True
        CACHE_EVENTS.labels(self.service_name, provider, "miss").inc()
        lock_name = f"provider:{provider}:{stable_hash(query)}"
        acquired = False
        try:
            acquired = await self.cache.acquire_lock(lock_name, ttl_seconds=15)
        except Exception:  # noqa: BLE001
            acquired = True
        if not acquired:
            # someone else is fetching: wait briefly for their result
            for _ in range(20):
                await asyncio.sleep(0.25)
                try:
                    cached = await self.cache.get_json(key)
                except Exception:  # noqa: BLE001
                    cached = None
                if cached is not None and not cached.get("negative"):
                    CACHE_EVENTS.labels(self.service_name, provider, "hit_after_wait").inc()
                    return [model.model_validate(r) for r in cached["records"]], True
        try:
            records = await fetch()
            payload = {"records": [orjson.loads(r.model_dump_json()) for r in records]}
            try:
                await self.cache.set_json(key, payload, ttl_seconds)
            except Exception:  # noqa: BLE001
                log.warning("cache_write_failed", provider=provider)
            return records, False
        except Exception as exc:
            if getattr(exc, "retryable", False):
                try:
                    await self.cache.set_json(key, {"negative": True, "error": type(exc).__name__}, negative_ttl)
                except Exception:  # noqa: BLE001
                    log.warning("negative_cache_write_failed", provider=provider)
            raise
        finally:
            if acquired:
                try:
                    await self.cache.release_lock(lock_name)
                except Exception:  # noqa: BLE001
                    log.warning("lock_release_failed", provider=provider)


class NegativeCachedError(Exception):
    pass
