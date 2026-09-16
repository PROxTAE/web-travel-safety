"""Post-validation of the LLM explanation against the locked decision and allowed facts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.llm.schemas import Explanation

BANNED_PHRASES = [
    r"\b100\s*%\s*safe\b",
    r"\bguarantee[ds]?\b",
    r"\bcompletely safe\b",
    r"\btotally safe\b",
    r"\bno risk at all\b",
    r"ปลอดภัย\s*100\s*%",
    r"รับประกัน",
    r"ปลอดภัยแน่นอน",
    r"ไม่มีความเสี่ยงเลย",
]
_NUMBER = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)(?![\w.])")
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s\-]{6,}\d)(?!\d)")
_CITE = re.compile(r"\[([0-9a-f\-]{36})\]")
_HTML = re.compile(r"<[^>]+>")


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    action_locked: bool = True
    citations_ok: bool = True
    numbers_ok: bool = True
    banned_ok: bool = True
    schema_ok: bool = True
    problems: list[str] = field(default_factory=list)


def allowed_numbers(facts: list[str], extra: list[str]) -> set[str]:
    out: set[str] = set()
    for text in [*facts, *extra]:
        for m in _NUMBER.finditer(text):
            raw = m.group(1).replace(",", ".")
            out.add(raw)
            try:
                v = float(raw)
                out.add(f"{v:.0f}")
                out.add(f"{v:.1f}")
                if v.is_integer():
                    out.add(str(int(v)))
            except ValueError:
                pass
    return out


def _numbers_in(text: str) -> list[str]:
    return [m.group(1).replace(",", ".") for m in _NUMBER.finditer(text)]


def validate_explanation(
    exp: Explanation,
    *,
    locked_action: str,
    allowed_citations: set[str],
    allowed_facts: list[str],
    extra_allowed_numbers: list[str],
    max_summary_words: int = 60,
) -> ValidationResult:
    r = ValidationResult(ok=True)
    texts = [exp.short_summary, *exp.reasons, *exp.immediate_actions, *exp.limitations]
    if exp.action_code != locked_action:
        r.action_locked = False
        r.problems.append(f"action {exp.action_code} != locked {locked_action}")
    # citations: explicit list and any inline [uuid] references must be a subset of the allowed ids
    inline = {m.group(1) for t in texts for m in _CITE.finditer(t)}
    for c in set(exp.citations_used) | inline:
        if c not in allowed_citations:
            r.citations_ok = False
            r.problems.append(f"unknown citation {c[:8]}")
    allowed_nums = allowed_numbers(allowed_facts, extra_allowed_numbers)
    for t in texts:
        for n in _numbers_in(t):
            if n not in allowed_nums:
                # small integers used as ordinals/counts (1-12) are tolerated
                try:
                    if float(n).is_integer() and 0 <= float(n) <= 12:
                        continue
                except ValueError:
                    pass
                r.numbers_ok = False
                r.problems.append(f"number {n} not in evidence")
    for t in texts:
        for pat in BANNED_PHRASES:
            if re.search(pat, t, re.IGNORECASE):
                r.banned_ok = False
                r.problems.append(f"banned phrase: {pat}")
        if _URL.search(t) or _PHONE.search(t) or _HTML.search(t):
            r.banned_ok = False
            r.problems.append("url/phone/html not allowed in explanation")
    if len(exp.short_summary.split()) > max_summary_words or not exp.short_summary.strip():
        r.schema_ok = False
        r.problems.append("summary length")
    r.ok = r.action_locked and r.citations_ok and r.numbers_ok and r.banned_ok and r.schema_ok
    return r
