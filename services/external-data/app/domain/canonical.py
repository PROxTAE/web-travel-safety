"""Canonical helpers: provenance/quality builders and versioned severity mapping tables.

Every adapter goes through these so provider-specific shapes never leak.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sta_contracts.enums import DataStatus, DisasterEventType, QualityFlag, Severity, SourceAuthority
from sta_contracts.models import DataQuality, SourceProvenance

SEVERITY_TABLE_VERSION = "1.0.0"


def content_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def deterministic_id(*parts: str) -> Any:
    return uuid5(NAMESPACE_URL, "sta:" + ":".join(parts))


def provenance(
    *,
    provider: str,
    record_id: str | None,
    authority: SourceAuthority,
    source_url: str | None,
    license_: str | None,
    attribution: str | None,
    observed_at: datetime | None,
    published_at: datetime | None,
    fetched_at: datetime,
    ttl_seconds: int,
    raw: Any,
) -> SourceProvenance:
    return SourceProvenance(
        source_id=deterministic_id(provider, record_id or content_hash(raw)),
        provider=provider,
        provider_record_id=record_id,
        authority=authority,
        source_url=source_url,
        license=license_,
        attribution=attribution,
        observed_at=observed_at,
        published_at=published_at,
        fetched_at=fetched_at,
        expires_at=fetched_at + timedelta(seconds=ttl_seconds),
        content_hash=content_hash(raw),
    )


def quality(
    *,
    observed_at: datetime | None,
    fetched_at: datetime,
    ttl_seconds: int,
    missing_fields: list[str] | None = None,
    total_fields: int | None = None,
    extra_flags: list[QualityFlag] | None = None,
    notes: list[str] | None = None,
    coverage: float | None = None,
) -> DataQuality:
    flags = list(extra_flags or [])
    notes_out = list(notes or [])
    freshness = None
    if observed_at is not None:
        freshness = max(0, int((fetched_at - observed_at.astimezone(UTC)).total_seconds()))
    else:
        flags.append(QualityFlag.MISSING)
        notes_out.append("observed_at not provided by provider")
    completeness = None
    if total_fields:
        completeness = round(1 - len(missing_fields or []) / total_fields, 3)
        if missing_fields:
            flags.append(QualityFlag.INCOMPLETE)
            notes_out.extend(f"{f} not returned by provider" for f in missing_fields)
    status = DataStatus.FRESH
    if freshness is not None and freshness > ttl_seconds:
        status = DataStatus.STALE
        flags.append(QualityFlag.STALE)
    # weights v1.0.0: freshness 0.5, completeness 0.3, coverage 0.2
    f_score = 1.0 if freshness is None else max(0.0, 1 - freshness / max(ttl_seconds, 1) / 2)
    c_score = completeness if completeness is not None else 1.0
    cov_score = coverage if coverage is not None else 1.0
    score = round(0.5 * f_score + 0.3 * c_score + 0.2 * cov_score, 3)
    if observed_at is None:
        score = round(score * 0.9, 3)
    return DataQuality(
        status=status,
        score=max(0.0, min(1.0, score)),
        flags=sorted(set(flags), key=lambda f: f.value),
        coverage=coverage,
        completeness=completeness,
        freshness_seconds=freshness,
        notes=notes_out,
        weights_version="1.0.0",
    )


# --------------------------------------------------------------------------- WMO weather codes
# https://open-meteo.com/en/docs — WMO 4677 code table as exposed by Open-Meteo.
WMO_CATEGORY: dict[int, tuple[str, Severity]] = {
    0: ("CLEAR", Severity.INFO),
    1: ("MAINLY_CLEAR", Severity.INFO),
    2: ("PARTLY_CLOUDY", Severity.INFO),
    3: ("OVERCAST", Severity.INFO),
    45: ("FOG", Severity.MINOR),
    48: ("RIME_FOG", Severity.MINOR),
    51: ("DRIZZLE", Severity.MINOR),
    53: ("DRIZZLE", Severity.MINOR),
    55: ("DRIZZLE", Severity.MINOR),
    56: ("FREEZING_DRIZZLE", Severity.MODERATE),
    57: ("FREEZING_DRIZZLE", Severity.MODERATE),
    61: ("RAIN", Severity.MINOR),
    63: ("RAIN", Severity.MODERATE),
    65: ("HEAVY_RAIN", Severity.SEVERE),
    66: ("FREEZING_RAIN", Severity.SEVERE),
    67: ("FREEZING_RAIN", Severity.SEVERE),
    71: ("SNOW", Severity.MINOR),
    73: ("SNOW", Severity.MODERATE),
    75: ("HEAVY_SNOW", Severity.SEVERE),
    77: ("SNOW_GRAINS", Severity.MINOR),
    80: ("RAIN_SHOWERS", Severity.MINOR),
    81: ("RAIN_SHOWERS", Severity.MODERATE),
    82: ("VIOLENT_RAIN_SHOWERS", Severity.SEVERE),
    85: ("SNOW_SHOWERS", Severity.MODERATE),
    86: ("HEAVY_SNOW_SHOWERS", Severity.SEVERE),
    95: ("THUNDERSTORM", Severity.SEVERE),
    96: ("THUNDERSTORM_HAIL", Severity.EXTREME),
    99: ("THUNDERSTORM_HEAVY_HAIL", Severity.EXTREME),
}


def wmo_category(code: int | None) -> tuple[str | None, Severity]:
    if code is None:
        return None, Severity.UNKNOWN
    return WMO_CATEGORY.get(int(code), ("UNKNOWN", Severity.UNKNOWN))


def escalate_weather_severity(
    base: Severity, *, wind_gust_kmh: float | None, precipitation_mm: float | None, visibility_m: float | None
) -> Severity:
    """Numeric thresholds (v1.0.0) that can only raise, never lower, the WMO-derived severity."""
    rank = {
        Severity.UNKNOWN: 0,
        Severity.INFO: 1,
        Severity.MINOR: 2,
        Severity.MODERATE: 3,
        Severity.SEVERE: 4,
        Severity.EXTREME: 5,
    }
    inv = {v: k for k, v in rank.items()}
    r = rank[base]
    if wind_gust_kmh is not None:
        if wind_gust_kmh >= 118:
            r = max(r, 5)
        elif wind_gust_kmh >= 89:
            r = max(r, 4)
        elif wind_gust_kmh >= 62:
            r = max(r, 3)
    if precipitation_mm is not None:
        if precipitation_mm >= 30:
            r = max(r, 4)
        elif precipitation_mm >= 10:
            r = max(r, 3)
    if visibility_m is not None:
        if visibility_m < 200:
            r = max(r, 4)
        elif visibility_m < 1000:
            r = max(r, 3)
    return inv[r]


# --------------------------------------------------------------------------- Disaster mappings
def usgs_severity(magnitude: float | None) -> Severity:
    if magnitude is None:
        return Severity.UNKNOWN
    if magnitude >= 7.0:
        return Severity.EXTREME
    if magnitude >= 6.0:
        return Severity.SEVERE
    if magnitude >= 5.0:
        return Severity.MODERATE
    if magnitude >= 4.0:
        return Severity.MINOR
    return Severity.INFO


GDACS_ALERT_SEVERITY: dict[str, Severity] = {
    "green": Severity.MINOR,
    "orange": Severity.SEVERE,
    "red": Severity.EXTREME,
}

GDACS_EVENT_TYPE: dict[str, DisasterEventType] = {
    "EQ": DisasterEventType.EARTHQUAKE,
    "TC": DisasterEventType.CYCLONE,
    "FL": DisasterEventType.FLOOD,
    "VO": DisasterEventType.VOLCANO,
    "DR": DisasterEventType.EXTREME_TEMPERATURE,
    "WF": DisasterEventType.WILDFIRE,
    "TS": DisasterEventType.OTHER,
}

EONET_CATEGORY_TYPE: dict[str, DisasterEventType] = {
    "severeStorms": DisasterEventType.STORM,
    "wildfires": DisasterEventType.WILDFIRE,
    "volcanoes": DisasterEventType.VOLCANO,
    "floods": DisasterEventType.FLOOD,
    "earthquakes": DisasterEventType.EARTHQUAKE,
    "landslides": DisasterEventType.LANDSLIDE,
    "seaLakeIce": DisasterEventType.OTHER,
    "snow": DisasterEventType.STORM,
    "tempExtremes": DisasterEventType.EXTREME_TEMPERATURE,
    "drought": DisasterEventType.EXTREME_TEMPERATURE,
    "dustHaze": DisasterEventType.OTHER,
    "manmade": DisasterEventType.OTHER,
    "waterColor": DisasterEventType.OTHER,
}


def eonet_severity(category: str, magnitude_value: float | None, unit: str | None) -> Severity:
    """EONET is curated metadata, not an official warning: cap at SEVERE unless a storm magnitude is hurricane force."""
    if category == "severeStorms" and magnitude_value is not None and (unit or "").lower() == "kts":
        if magnitude_value >= 64:
            return Severity.SEVERE
        if magnitude_value >= 34:
            return Severity.MODERATE
        return Severity.MINOR
    if category in ("wildfires", "volcanoes", "floods"):
        return Severity.MODERATE
    return Severity.MINOR
