from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sta_common.logging import get_logger
from sta_contracts.models import ProviderHealth

from app.adapters.base import ProviderDescriptor
from app.repositories.models import FetchLog, Provider, ProviderHealthRow

log = get_logger("provider-repo")


class ProviderRepository:
    """Best-effort persistence. Provider metadata/health/fetch-log writes never block a live query."""

    def __init__(self, factory: async_sessionmaker[AsyncSession] | None) -> None:
        self.factory = factory

    async def upsert_providers(self, descriptors: list[ProviderDescriptor]) -> None:
        if self.factory is None:
            return
        async with self.factory() as s:
            for d in descriptors:
                stmt = insert(Provider).values(
                    id=d.name,
                    kind=d.kind.value,
                    name=d.name,
                    enabled=d.enabled,
                    coverage_json={"note": d.coverage_note, "version": d.version},
                    license_url=d.docs_url,
                    attribution=d.attribution,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=[Provider.id],
                    set_={
                        "enabled": d.enabled,
                        "coverage_json": {"note": d.coverage_note, "version": d.version},
                        "attribution": d.attribution,
                    },
                )
                await s.execute(stmt)
            await s.commit()

    async def record_health(self, rows: list[ProviderHealth]) -> None:
        if self.factory is None:
            return
        try:
            async with self.factory() as s:
                for h in rows:
                    stmt = insert(ProviderHealthRow).values(
                        provider_id=h.provider,
                        status=h.status,
                        latency_ms=h.latency_ms,
                        quota_remaining=h.quota_remaining,
                        last_error_code=h.last_error_code,
                        checked_at=h.checked_at,
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[ProviderHealthRow.provider_id],
                        set_={
                            "status": h.status,
                            "latency_ms": h.latency_ms,
                            "quota_remaining": h.quota_remaining,
                            "last_error_code": h.last_error_code,
                            "checked_at": h.checked_at,
                        },
                    )
                    await s.execute(stmt)
                await s.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("health_persist_failed", error_type=type(exc).__name__)

    async def log_fetch(
        self,
        *,
        provider: str,
        request_id: str | None,
        query_hash: str,
        outcome: str,
        error_code: str | None,
        record_count: int,
        from_cache: bool,
        latency_ms: float | None,
        expires_at: datetime | None,
        content_hash: str | None,
        quality: dict[str, Any] | None,
    ) -> None:
        if self.factory is None:
            return
        try:
            async with self.factory() as s:
                s.add(
                    FetchLog(
                        provider_id=provider,
                        request_id=request_id,
                        query_hash=query_hash,
                        outcome=outcome,
                        error_code=error_code,
                        record_count=record_count,
                        from_cache=from_cache,
                        latency_ms=latency_ms,
                        fetched_at=datetime.now(UTC),
                        expires_at=expires_at,
                        content_hash=content_hash,
                        quality_json=quality or {},
                    )
                )
                await s.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("fetch_log_failed", provider=provider, error_type=type(exc).__name__)
