"""UTC helpers. All API timestamps are ISO-8601 with Z; DB stores UTC."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must carry a timezone offset")
    return dt.astimezone(UTC)


def age_seconds(dt: datetime | None, now: datetime | None = None) -> int | None:
    if dt is None:
        return None
    now = now or utcnow()
    return max(0, int((now - dt.astimezone(UTC)).total_seconds()))


def plus(seconds: int, base: datetime | None = None) -> datetime:
    return (base or utcnow()) + timedelta(seconds=seconds)
