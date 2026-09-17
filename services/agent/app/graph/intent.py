"""Intent classification: deterministic rules first (UI hints, keywords TH/EN); optional structured LLM classifier only
for ambiguous free text. Structured request fields are the source of truth; text never overrides confirmed
coordinates/time. Provider/RAG text is never fed to the classifier."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sta_contracts.enums import Intent
from sta_contracts.models import TravelRequest

EMERGENCY_TERMS = re.compile(
    r"\b(sos|emergency|help me|accident|injured|ambulance|police now)\b|ฉุกเฉิน|ช่วยด้วย|อุบัติเหตุ|บาดเจ็บ|รถพยาบาล",
    re.IGNORECASE,
)
SAFETY_TERMS = re.compile(
    r"\b(safe|safety|risk|storm|flood|rain|typhoon|earthquake|danger|closure|closed)\b|ปลอดภัย|เสี่ยง|พายุ|น้ำท่วม|ฝน|แผ่นดินไหว|อันตราย|ปิดถนน",
    re.IGNORECASE,
)
INFO_TERMS = re.compile(
    r"\b(what|why|how|explain|which document|source|tell me)\b|อะไร|ทำไม|อย่างไร|ยังไง|อธิบาย|แหล่งที่มา", re.IGNORECASE
)
INJECTION = re.compile(
    r"ignore (all|previous|the) (rules|instructions)|system prompt|you are now|disregard", re.IGNORECASE
)

CRITICAL_FIELDS = ("origin", "destination", "departure_time", "travel_modes")


@dataclass(slots=True)
class IntentResult:
    intent: Intent
    method: str  # HINT | RULES | LLM | DEFAULT
    injection_flagged: bool


def classify(req: TravelRequest, *, is_followup: bool) -> IntentResult:
    q = req.question or ""
    flagged = bool(INJECTION.search(q))
    if EMERGENCY_TERMS.search(q):
        return IntentResult(Intent.EMERGENCY, "RULES", flagged)
    if req.intent_hint is not None:
        return IntentResult(req.intent_hint, "HINT", flagged)
    if is_followup:
        if INFO_TERMS.search(q) and not SAFETY_TERMS.search(q):
            return IntentResult(Intent.ASK_INFORMATION, "RULES", flagged)
        return IntentResult(Intent.FOLLOW_UP, "RULES", flagged)
    if not q:
        return IntentResult(Intent.PLAN_TRIP, "DEFAULT", flagged)
    if SAFETY_TERMS.search(q):
        return IntentResult(Intent.CHECK_SAFETY, "RULES", flagged)
    if INFO_TERMS.search(q):
        return IntentResult(Intent.ASK_INFORMATION, "RULES", flagged)
    return IntentResult(Intent.CHECK_SAFETY, "DEFAULT", flagged)


def missing_critical(req: TravelRequest) -> list[str]:
    missing: list[str] = []
    if not req.origin.confirmed_by_user:
        missing.append("origin.confirmed_by_user")
    if not req.destination.confirmed_by_user:
        missing.append("destination.confirmed_by_user")
    if not req.travel_modes:
        missing.append("travel_modes")
    if not req.timezone:
        missing.append("timezone")
    return missing


def needs_fresh_data(intent: Intent, question: str | None) -> bool:
    """Current-safety questions always refresh; purely informational follow-ups may reuse fresh evidence."""
    if intent in (Intent.PLAN_TRIP, Intent.CHECK_SAFETY, Intent.EMERGENCY):
        return True
    if intent == Intent.FOLLOW_UP:
        return (
            bool(question and SAFETY_TERMS.search(question))
            or re.search(r"\b(now|current|today|latest|update)\b|ตอนนี้|ล่าสุด|วันนี้|อัปเดต", question or "", re.IGNORECASE)
            is not None
        )
    return False
