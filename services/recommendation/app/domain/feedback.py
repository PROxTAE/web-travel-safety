"""Feedback governance: sanitize/limit text, pseudonymous linkage, UNSAFE → safety review queue, no live learning."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from sta_common.redaction import redact_text
from sta_contracts.enums import FeedbackCategory

MAX_TEXT = 1000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
REVIEW_CATEGORIES = {
    FeedbackCategory.UNSAFE: "HIGH",
    FeedbackCategory.INCORRECT: "MEDIUM",
    FeedbackCategory.ROUTE_ISSUE: "MEDIUM",
}


@dataclass(slots=True)
class PreparedFeedback:
    category: FeedbackCategory
    text_redacted: str | None
    user_pseudonym: str
    review_severity: str | None


def pseudonym(user_scope: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{user_scope}".encode()).hexdigest()[:32]


def prepare(category: FeedbackCategory, text: str | None, *, user_scope: str, salt: str) -> PreparedFeedback:
    cleaned: str | None = None
    if text:
        cleaned = redact_text(_CONTROL.sub("", text)).strip()[:MAX_TEXT] or None
    return PreparedFeedback(
        category=category,
        text_redacted=cleaned,
        user_pseudonym=pseudonym(user_scope, salt),
        review_severity=REVIEW_CATEGORIES.get(category),
    )
