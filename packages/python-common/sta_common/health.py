"""Health endpoints: /health/live checks the process; /health/ready checks bounded dependencies."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import APIRouter
from fastapi.responses import ORJSONResponse

from sta_common.metrics import READINESS

ReadyCheck = Callable[[], Awaitable[None]]


@dataclass(slots=True)
class DependencyCheck:
    name: str
    check: ReadyCheck
    critical: bool = True
    timeout_seconds: float = 2.0


class HealthRegistry:
    def __init__(self, service_name: str, version: str) -> None:
        self.service_name = service_name
        self.version = version
        self.started_at = time.time()
        self._checks: list[DependencyCheck] = []

    def add(self, name: str, check: ReadyCheck, *, critical: bool = True, timeout_seconds: float = 2.0) -> None:
        self._checks.append(DependencyCheck(name, check, critical, timeout_seconds))

    async def evaluate(self) -> tuple[bool, dict[str, dict[str, object]]]:
        async def run(dc: DependencyCheck) -> tuple[str, dict[str, object]]:
            started = time.perf_counter()
            try:
                await asyncio.wait_for(dc.check(), timeout=dc.timeout_seconds)
                return dc.name, {
                    "status": "up",
                    "critical": dc.critical,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                }
            except TimeoutError:
                return dc.name, {"status": "timeout", "critical": dc.critical}
            except Exception as exc:  # noqa: BLE001 - surface type only, never message internals
                return dc.name, {"status": "down", "critical": dc.critical, "error": type(exc).__name__}

        results = dict(await asyncio.gather(*(run(c) for c in self._checks)))
        ready = all(r["status"] == "up" for r in results.values() if r["critical"])
        READINESS.labels(self.service_name).set(1 if ready else 0)
        return ready, results

    def router(self) -> APIRouter:
        r = APIRouter(tags=["health"])

        @r.get("/health/live")
        async def live() -> dict[str, object]:
            return {
                "status": "ok",
                "service": self.service_name,
                "version": self.version,
                "uptime_seconds": round(time.time() - self.started_at),
            }

        @r.get("/health/ready")
        async def ready() -> ORJSONResponse:
            ok, deps = await self.evaluate()
            body = {
                "status": "ready" if ok else "not_ready",
                "service": self.service_name,
                "version": self.version,
                "dependencies": deps,
            }
            return ORJSONResponse(body, status_code=200 if ok else 503)

        return r
