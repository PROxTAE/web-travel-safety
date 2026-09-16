"""Quality score + gate (versioned weights). PASS / DEGRADED / BLOCK.

Score = w_fresh*freshness + w_complete*completeness + w_cov*coverage + w_agree*agreement + w_auth*authority
Gate:
  BLOCK    — no usable route, or snapshot score < gate_block_score, or weather+events both unavailable
  DEGRADED — score < gate_degraded_score, or weather coverage < min, or stale/conflicting critical data
  PASS     — otherwise
"""

from __future__ import annotations

from datetime import datetime
from statistics import fmean

from sta_contracts.enums import AUTHORITY_RANK, DataStatus, QualityFlag, QualityGate
from sta_contracts.models import DataQuality, DisasterEvent, RouteCandidate, TransportStatus, WeatherForecastPoint

from app.settings import Settings

WEIGHTS_VERSION = "1.0.0"
W = {"fresh": 0.35, "complete": 0.2, "coverage": 0.2, "agreement": 0.15, "authority": 0.1}


def _freshness_score(qualities: list[DataQuality], ttl: int) -> float:
    if not qualities:
        return 0.0
    vals = []
    for q in qualities:
        if q.freshness_seconds is None:
            vals.append(0.7)
        else:
            vals.append(max(0.0, 1 - q.freshness_seconds / (2 * ttl)))
    return fmean(vals)


def summarize_group(qualities: list[DataQuality], *, ttl: int, coverage: float | None, notes: list[str]) -> DataQuality:
    if not qualities:
        return DataQuality(
            status=DataStatus.UNAVAILABLE,
            score=0.0,
            flags=[QualityFlag.MISSING],
            coverage=0.0,
            completeness=0.0,
            notes=notes + ["no records"],
            weights_version=WEIGHTS_VERSION,
        )
    flags: set[QualityFlag] = set()
    for q in qualities:
        flags.update(q.flags)
    completeness = fmean([q.completeness if q.completeness is not None else 1.0 for q in qualities])
    cov = (
        coverage if coverage is not None else fmean([q.coverage if q.coverage is not None else 1.0 for q in qualities])
    )
    stale_ratio = sum(1 for q in qualities if q.status == DataStatus.STALE) / len(qualities)
    unavailable = all(q.status == DataStatus.UNAVAILABLE for q in qualities)
    conflicting = any(QualityFlag.CONFLICTING in q.flags for q in qualities)
    score = (
        W["fresh"] * _freshness_score(qualities, ttl)
        + W["complete"] * completeness
        + W["coverage"] * cov
        + W["agreement"] * (0.5 if conflicting else 1.0)
        + W["authority"] * 1.0
    )
    if unavailable:
        status = DataStatus.UNAVAILABLE
        score = 0.0
    elif conflicting:
        status = DataStatus.CONFLICTING
    elif stale_ratio > 0.5:
        status = DataStatus.STALE
    elif cov < 1.0 or stale_ratio > 0:
        status = DataStatus.PARTIAL
    else:
        status = DataStatus.FRESH
    return DataQuality(
        status=status,
        score=round(max(0.0, min(1.0, score)), 3),
        flags=sorted(flags, key=lambda f: f.value),
        coverage=round(cov, 3),
        completeness=round(completeness, 3),
        freshness_seconds=max((q.freshness_seconds or 0) for q in qualities),
        notes=notes,
        weights_version=WEIGHTS_VERSION,
    )


def overall_and_gate(
    s: Settings,
    *,
    route_q: DataQuality,
    weather_q: DataQuality,
    transport_q: DataQuality,
    disaster_q: DataQuality,
    routes: list[RouteCandidate],
    weather_coverage: float,
    conflicts_safety_critical: int,
    official_alerts: list[DisasterEvent],
    now: datetime,
) -> tuple[DataQuality, QualityGate, list[str]]:
    reasons: list[str] = []
    usable_routes = [r for r in routes if r.usable]
    # transport is informative only in v1 (coverage is rare) -> lower weight in the overall mix
    parts = [(route_q, 0.35), (weather_q, 0.35), (disaster_q, 0.2), (transport_q, 0.1)]
    score = round(sum(q.score * w for q, w in parts), 3)
    flags: set[QualityFlag] = set()
    for q, _ in parts:
        flags.update(q.flags)
    auth_bonus = 0.0
    if official_alerts:
        auth_bonus = 0.02 * max(AUTHORITY_RANK[a.source.authority] for a in official_alerts) / 4
    score = round(min(1.0, score + auth_bonus), 3)

    gate = QualityGate.PASS
    if not usable_routes:
        gate = QualityGate.BLOCK
        reasons.append("NO_USABLE_ROUTE")
    elif weather_q.status == DataStatus.UNAVAILABLE and disaster_q.status == DataStatus.UNAVAILABLE:
        gate = QualityGate.BLOCK
        reasons.append("NO_WEATHER_AND_NO_DISASTER_EVIDENCE")
    elif score < s.gate_block_score:
        gate = QualityGate.BLOCK
        reasons.append(f"SCORE_BELOW_BLOCK_THRESHOLD:{score}<{s.gate_block_score}")
    else:
        if score < s.gate_degraded_score:
            gate = QualityGate.DEGRADED
            reasons.append(f"SCORE_BELOW_DEGRADED_THRESHOLD:{score}<{s.gate_degraded_score}")
        if weather_coverage < s.min_weather_coverage_for_pass:
            gate = QualityGate.DEGRADED
            reasons.append(f"WEATHER_COVERAGE_LOW:{weather_coverage:.2f}")
        if conflicts_safety_critical:
            gate = QualityGate.DEGRADED
            reasons.append(f"SAFETY_CRITICAL_CONFLICTS:{conflicts_safety_critical}")
        if any(QualityFlag.INFERRED in r.quality.flags for r in usable_routes):
            gate = QualityGate.DEGRADED
            reasons.append("ROUTE_GEOMETRY_INFERRED")
        stale_official = [a for a in official_alerts if a.quality.status == DataStatus.STALE]
        if stale_official:
            gate = QualityGate.DEGRADED
            reasons.append(f"STALE_OFFICIAL_ALERTS:{len(stale_official)}")
    status = {
        QualityGate.PASS: DataStatus.FRESH,
        QualityGate.DEGRADED: DataStatus.PARTIAL,
        QualityGate.BLOCK: DataStatus.UNAVAILABLE,
    }[gate]
    if conflicts_safety_critical and gate != QualityGate.BLOCK:
        status = DataStatus.CONFLICTING
    overall = DataQuality(
        status=status,
        score=score,
        flags=sorted(flags, key=lambda f: f.value),
        coverage=round(weather_coverage, 3),
        completeness=round(fmean([q.completeness or 0.0 for q, _ in parts]), 3),
        freshness_seconds=max(q.freshness_seconds or 0 for q, _ in parts),
        notes=reasons,
        weights_version=WEIGHTS_VERSION,
    )
    return overall, gate, reasons


__all__ = ["summarize_group", "overall_and_gate", "WEIGHTS_VERSION", "TransportStatus", "WeatherForecastPoint"]
