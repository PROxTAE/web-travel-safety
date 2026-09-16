"""Arq worker: periodic subscription refresh -> publishes `alert.reassessment.requested` to a Redis stream.

The API (คน 2) consumes the stream, starts a fresh assessment for the trip, and calls
POST /internal/v1/alerts/evaluate with previous/new recommendation ids. The worker never fetches
provider data itself and never sends user data beyond trip id + reason.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arq import cron
from arq.connections import RedisSettings
from sta_common.db import build_dsn, create_engine, session_factory
from sta_common.logging import configure_logging, get_logger

from app.repositories.repo import Repository
from app.settings import get_settings

log = get_logger("recommendation-worker")
HEARTBEAT = Path("/tmp/worker-heartbeat")  # noqa: S108 - container-local heartbeat for HEALTHCHECK


async def refresh_subscriptions(ctx: dict[str, Any]) -> int:
    s = get_settings()
    repo: Repository = ctx["repo"]
    redis = ctx["redis"]
    subs = await repo.all_active_subscriptions()
    trips = sorted({str(sub.trip_id) for sub in subs})
    stream = f"sta:{s.app_env}:stream:{s.reassessment_stream}"
    for trip_id in trips:
        await redis.xadd(
            stream,
            {
                "event_id": f"{trip_id}:{int(time.time())}",
                "event_type": s.reassessment_stream,
                "occurred_at": datetime.now(UTC).isoformat(),
                "producer": "recommendation-worker",
                "schema_version": "1.0.0",
                "payload": json.dumps({"trip_id": trip_id, "reason": "SUBSCRIPTION_REFRESH"}),
            },
            maxlen=10_000,
            approximate=True,
        )
    await asyncio.to_thread(HEARTBEAT.write_text, str(time.time()))
    log.info("subscriptions_refreshed", trips=len(trips), subscriptions=len(subs))
    return len(trips)


async def startup(ctx: dict[str, Any]) -> None:
    s = get_settings()
    configure_logging("recommendation-worker", s.app_env, s.log_level)
    dsn = build_dsn(
        host=s.postgres_host,
        port=s.postgres_port,
        db=s.postgres_db,
        user=s.db_user,
        password=s.postgres_recommendation_password.get_secret_value(),
    )
    ctx["engine"] = create_engine(dsn, schema="recommendation")
    ctx["repo"] = Repository(session_factory(ctx["engine"]))
    await asyncio.to_thread(HEARTBEAT.write_text, str(time.time()))


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["engine"].dispose()


_every = max(1, get_settings().subscription_refresh_minutes)


class WorkerSettings:
    functions = [refresh_subscriptions]
    cron_jobs = [cron(refresh_subscriptions, minute=set(range(0, 60, _every)), run_at_startup=True)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 2
    job_timeout = 120


if __name__ == "__main__":
    from arq.worker import run_worker

    run_worker(WorkerSettings)  # type: ignore[arg-type]
