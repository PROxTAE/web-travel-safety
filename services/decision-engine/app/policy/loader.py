"""Policy registry: load YAML, validate against JSON Schema, compute checksum, require approval."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    priority: int
    when: dict[str, Any]
    action: str
    select: str | None
    escalate: bool
    reason_codes: tuple[str, ...]
    specificity: float


@dataclass(frozen=True, slots=True)
class Policy:
    version: str
    checksum: str
    approved_by: str
    approved_at: str
    thresholds: dict[str, float]
    rules: tuple[Rule, ...]
    status: str = "APPROVED"


def load_policy(path: str | Path, schema_path: str | Path) -> Policy:
    raw_bytes = Path(path).read_bytes()
    raw = yaml.safe_load(raw_bytes)
    schema = yaml.safe_load(Path(schema_path).read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(raw), key=lambda e: list(e.path))
    if errors:
        raise ValueError("policy schema invalid: " + "; ".join(e.message for e in errors[:5]))
    if not raw.get("approved_by"):
        raise ValueError("policy has no approval record")
    priorities = [r["priority"] for r in raw["rules"]]
    if len(priorities) != len(set(priorities)):
        raise ValueError("policy rule priorities must be unique (first-match semantics)")
    rules = tuple(
        Rule(
            id=r["id"],
            priority=int(r["priority"]),
            when=dict(r["when"]),
            action=r["action"],
            select=r.get("select"),
            escalate=bool(r["escalate"]),
            reason_codes=tuple(r["reason_codes"]),
            specificity=float(r["specificity"]),
        )
        for r in sorted(raw["rules"], key=lambda r: r["priority"])
    )
    return Policy(
        version=str(raw["version"]),
        checksum=hashlib.sha256(raw_bytes).hexdigest(),
        approved_by=str(raw["approved_by"]),
        approved_at=str(raw["approved_at"]),
        thresholds={k: float(v) for k, v in raw["thresholds"].items()},
        rules=rules,
    )
