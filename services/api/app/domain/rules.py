"""Domain rules independent of FastAPI/DB: trip validation, request state machine, idempotency fingerprint."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import orjson
from sta_common.errors import AppError, ErrorCode, FieldError
from sta_contracts.enums import RunStatus
from sta_contracts.models import LocationRef

_ZONE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-/]{1,63}$")
_LOCALE_RE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")

REQUEST_TRANSITIONS: dict[str, frozenset[str]] = {
    "CREATED": frozenset({"QUEUED", "FAILED"}),
    "QUEUED": frozenset({"RUNNING", "NEEDS_INPUT", "COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}),
    "RUNNING": frozenset({"NEEDS_INPUT", "COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}),
    "NEEDS_INPUT": frozenset({"CANCELLED"}),
    "COMPLETED": frozenset(),
    "PARTIAL": frozenset(),
    "FAILED": frozenset(),
    "CANCELLED": frozenset(),
}
TERMINAL = frozenset({"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"})


def assert_transition(current: str, new: str) -> None:
    if new == current:
        return
    if new not in REQUEST_TRANSITIONS.get(current, frozenset()):
        raise AppError(ErrorCode.CONFLICT, f"request status {current} cannot move to {new}")


def valid_timezone(tz: str) -> str:
    if not _ZONE_RE.match(tz):
        raise AppError(
            ErrorCode.VALIDATION_ERROR, "invalid timezone", field_errors=[FieldError(path="timezone", code="INVALID")]
        )
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise AppError(
            ErrorCode.VALIDATION_ERROR, "unknown timezone", field_errors=[FieldError(path="timezone", code="UNKNOWN")]
        ) from None
    return tz


def valid_locale(locale: str) -> str:
    if not _LOCALE_RE.match(locale):
        raise AppError(
            ErrorCode.VALIDATION_ERROR, "invalid locale", field_errors=[FieldError(path="locale", code="INVALID")]
        )
    return locale


def validate_location(loc: LocationRef, path: str, *, require_confirmed: bool) -> None:
    errs: list[FieldError] = []
    lon, lat = loc.coordinates.lon, loc.coordinates.lat
    if lon == 0 and lat == 0:
        errs.append(FieldError(path=f"{path}.coordinates", code="NULL_ISLAND"))
    if require_confirmed and not loc.confirmed_by_user:
        errs.append(FieldError(path=f"{path}.confirmed_by_user", code="UNCONFIRMED"))
    if errs:
        raise AppError(ErrorCode.VALIDATION_ERROR, "location not acceptable", field_errors=errs)


def validate_trip_times(
    departure: datetime,
    return_time: datetime | None,
    *,
    now: datetime | None = None,
    max_horizon_days: int,
    max_past_minutes: int,
) -> None:
    now = now or datetime.now(UTC)
    errs: list[FieldError] = []
    if departure < now - timedelta(minutes=max_past_minutes):
        errs.append(FieldError(path="departure_time", code="IN_PAST"))
    if departure > now + timedelta(days=max_horizon_days):
        errs.append(FieldError(path="departure_time", code="BEYOND_HORIZON"))
    if return_time is not None and return_time <= departure:
        errs.append(FieldError(path="return_time", code="BEFORE_DEPARTURE"))
    if errs:
        raise AppError(ErrorCode.VALIDATION_ERROR, "trip times not acceptable", field_errors=errs)


def idempotency_fingerprint(user_id: str, key: str, payload: Any) -> str:
    body = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(b"|".join([user_id.encode(), key.encode(), body])).hexdigest()


def parse_bbox(raw: str, *, max_degrees: float) -> tuple[float, float, float, float]:
    parts = raw.split(",")
    if len(parts) != 4:
        raise AppError(ErrorCode.VALIDATION_ERROR, "bbox must be min_lon,min_lat,max_lon,max_lat")
    try:
        min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
    except ValueError:
        raise AppError(ErrorCode.VALIDATION_ERROR, "bbox values must be numbers") from None
    if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
        raise AppError(ErrorCode.VALIDATION_ERROR, "bbox out of range")
    if max_lon - min_lon > max_degrees or max_lat - min_lat > max_degrees:
        raise AppError(ErrorCode.VALIDATION_ERROR, f"bbox larger than {max_degrees} degrees")
    return min_lon, min_lat, max_lon, max_lat


def run_status_to_request(status: RunStatus) -> str:
    return status.value
