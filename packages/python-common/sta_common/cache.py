"""Redis helpers: environment-prefixed keys (00_API_AND_DATA_CONTRACTS §7), TTL-only writes, locks."""

from __future__ import annotations

import hashlib
from typing import Any

import orjson
from redis.asyncio import Redis


def key(env: str, *parts: str) -> str:
    return "sta:" + env + ":" + ":".join(p.replace(":", "_") for p in parts)


def stable_hash(payload: Any) -> str:
    data = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(data).hexdigest()


class Cache:
    def __init__(self, redis: Redis, env: str, service: str) -> None:
        self.redis = redis
        self.env = env
        self.service = service

    def k(self, *parts: str) -> str:
        return key(self.env, *parts)

    async def get_json(self, k: str) -> Any | None:
        raw = await self.redis.get(k)
        return orjson.loads(raw) if raw else None

    async def set_json(self, k: str, value: Any, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise ValueError("cache writes must carry a positive TTL")
        await self.redis.set(k, orjson.dumps(value), ex=ttl_seconds)

    async def acquire_lock(self, name: str, ttl_seconds: int = 10) -> bool:
        return bool(await self.redis.set(self.k("lock", name), "1", ex=ttl_seconds, nx=True))

    async def release_lock(self, name: str) -> None:
        await self.redis.delete(self.k("lock", name))

    async def ping(self) -> None:
        await self.redis.ping()


def make_redis(url: str) -> Redis:
    client: Redis = Redis.from_url(
        url, decode_responses=False, socket_connect_timeout=2, socket_timeout=3, health_check_interval=30
    )
    return client
