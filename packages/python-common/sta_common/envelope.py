"""Standard success envelopes (00_API_AND_DATA_CONTRACTS §1)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sta_common.context import get_context


def meta(contract_version: str, degraded_services: list[str] | None = None) -> dict[str, Any]:
    ctx = get_context()
    return {
        "request_id": ctx.request_id,
        "correlation_id": ctx.correlation_id,
        "contract_version": contract_version,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "degraded_services": degraded_services or [],
    }


def ok(data: Any, contract_version: str, degraded_services: list[str] | None = None) -> dict[str, Any]:
    return {"data": data, "meta": meta(contract_version, degraded_services)}


def ok_list(
    items: list[Any],
    contract_version: str,
    *,
    cursor: str | None = None,
    next_cursor: str | None = None,
    degraded_services: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "data": items,
        "meta": meta(contract_version, degraded_services),
        "page": {"cursor": cursor, "next_cursor": next_cursor, "has_more": next_cursor is not None},
    }
