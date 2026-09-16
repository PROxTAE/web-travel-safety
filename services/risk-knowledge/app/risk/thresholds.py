"""Versioned thresholds + override tables loaded from YAML (never literals in code)."""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sta_contracts.enums import ReasonCode, RiskLevel

RISK_RANK = {RiskLevel.UNKNOWN: 0, RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3}
_OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt, "==": operator.eq}
_COND = re.compile(r"^\s*([a-z_0-9]+)\s*(>=|<=|==|>|<)\s*(-?\d+(\.\d+)?)\s*$")


@dataclass(frozen=True, slots=True)
class Thresholds:
    version: str
    high: float
    medium: float
    uncertainty_band: float
    delay_improvement_min: float

    def level(self, p: float) -> RiskLevel:
        if p >= self.high:
            return RiskLevel.HIGH
        if p >= self.medium:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def near_threshold(self, p: float) -> bool:
        return min(abs(p - self.high), abs(p - self.medium)) < self.uncertainty_band


@dataclass(frozen=True, slots=True)
class OverrideRule:
    id: str
    reason_code: ReasonCode
    minimum_risk: RiskLevel
    feature: str
    op: str
    value: float

    def fires(self, features: dict[str, Any]) -> bool:
        v = features.get(self.feature)
        if v is None:
            return False
        return bool(_OPS[self.op](float(v), self.value))


@dataclass(frozen=True, slots=True)
class Overrides:
    version: str
    rules: tuple[OverrideRule, ...]
    weather_coverage_below: float
    missing_critical_at_least: int
    result_when_low: RiskLevel


def load_thresholds(path: str | Path) -> Thresholds:
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Thresholds(
        version=str(d["version"]),
        high=float(d["high"]),
        medium=float(d["medium"]),
        uncertainty_band=float(d["uncertainty_band"]),
        delay_improvement_min=float(d["delay_improvement_min"]),
    )


def load_overrides(path: str | Path) -> Overrides:
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    rules = []
    for r in d["rules"]:
        m = _COND.match(str(r["when"]))
        if not m:
            raise ValueError(f"override rule {r['id']} has an unparsable condition: {r['when']}")
        rules.append(
            OverrideRule(
                id=str(r["id"]),
                reason_code=ReasonCode(r["reason_code"]),
                minimum_risk=RiskLevel(r["minimum_risk"]),
                feature=m.group(1),
                op=m.group(2),
                value=float(m.group(3)),
            )
        )
    ie = d["insufficient_evidence"]
    return Overrides(
        version=str(d["version"]),
        rules=tuple(rules),
        weather_coverage_below=float(ie["weather_coverage_below"]),
        missing_critical_at_least=int(ie["missing_critical_at_least"]),
        result_when_low=RiskLevel(ie["result_when_low"]),
    )


def max_level(a: RiskLevel, b: RiskLevel) -> RiskLevel:
    return a if RISK_RANK[a] >= RISK_RANK[b] else b
