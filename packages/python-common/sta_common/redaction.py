"""PII / secret redaction used by logs, traces, audit rows and error messages.

Rules (00_SHARED_PROJECT_CONTEXT §11): redact tokens, emails, phone numbers,
medical notes and exact coordinates. Coordinates are coarsened to 2 decimals
(~1 km) rather than dropped so that debugging by region stays possible.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\w/])\+?\d[\d\s().-]{6,}\d(?![\w/])")
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
_OPENAI_KEY = re.compile(r"sk-[A-Za-z0-9_-]{16,}")

SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "access_token",
        "refresh_token",
        "id_token",
        "token",
        "password",
        "secret",
        "client_secret",
        "api_key",
        "apikey",
        "x-api-key",
        "cookie",
        "set-cookie",
        "email",
        "phone",
        "medical_notes",
        "medical_note",
        "encrypted_payload",
        "blood_type",
        "allergies",
        "medications",
    }
)
COORDINATE_KEYS = frozenset({"lat", "latitude", "lon", "lng", "longitude", "coordinates"})

REDACTED = "[REDACTED]"


def redact_text(value: str) -> str:
    value = _JWT.sub(REDACTED, value)
    value = _BEARER.sub("Bearer " + REDACTED, value)
    value = _OPENAI_KEY.sub(REDACTED, value)
    value = _EMAIL.sub(REDACTED, value)
    value = _PHONE.sub(REDACTED, value)
    return value


def coarsen_coordinate(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return round(float(value), 2)
    if isinstance(value, list | tuple):
        return [coarsen_coordinate(v) for v in value]
    return value


def redact(obj: Any, *, _depth: int = 0) -> Any:
    """Recursively redact a structure. Safe on arbitrary JSON-like input."""
    if _depth > 12:
        return REDACTED
    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            key = str(k)
            lk = key.lower()
            if lk in SENSITIVE_KEYS:
                out[key] = REDACTED
            elif lk in COORDINATE_KEYS:
                out[key] = coarsen_coordinate(v)
            else:
                out[key] = redact(v, _depth=_depth + 1)
        return out
    if isinstance(obj, list | tuple):
        return [redact(v, _depth=_depth + 1) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj
