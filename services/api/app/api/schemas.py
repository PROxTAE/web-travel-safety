"""Public request bodies (responses reuse the shared contract models)."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sta_contracts.enums import ConsentType, DeliveryChannel, FeedbackCategory, Intent, Severity, TravelMode
from sta_contracts.models import LocationRef, TravelPreference

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


def clean_text(v: str | None) -> str | None:
    if v is None:
        return None
    v = unicodedata.normalize("NFC", _CONTROL.sub("", v)).strip()
    return v or None


class MePatch(Body):
    locale: str | None = Field(default=None, pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    timezone: str | None = Field(default=None, max_length=64)
    display_name: str | None = Field(default=None, max_length=120)

    @field_validator("display_name")
    @classmethod
    def _dn(cls, v: str | None) -> str | None:
        return clean_text(v)


class ConsentCreate(Body):
    type: ConsentType
    granted: bool
    policy_version: str = Field(min_length=1, max_length=32)
    expires_at: datetime | None = None


class EmergencyContactIn(Body):
    name: str = Field(min_length=1, max_length=120)
    relationship: str | None = Field(default=None, max_length=60)
    phone: str = Field(min_length=3, max_length=32, pattern=r"^\+?[0-9 ()\-]{3,32}$")


class EmergencyProfilePut(Body):
    blood_type: str | None = Field(default=None, max_length=8)
    allergies: list[str] = Field(default_factory=list, max_length=20)
    medications: list[str] = Field(default_factory=list, max_length=20)
    medical_notes: str | None = Field(default=None, max_length=2000)
    contacts: list[EmergencyContactIn] = Field(default_factory=list, max_length=5)
    insurance_provider: str | None = Field(default=None, max_length=120)
    insurance_policy_ref: str | None = Field(default=None, max_length=120)
    insurance_phone: str | None = Field(default=None, max_length=32)

    @field_validator("medical_notes", "blood_type", "insurance_provider", "insurance_policy_ref", "insurance_phone")
    @classmethod
    def _txt(cls, v: str | None) -> str | None:
        return clean_text(v)


class TripCreate(Body):
    origin: LocationRef
    destination: LocationRef
    departure_time: datetime
    return_time: datetime | None = None
    travel_modes: list[TravelMode] = Field(min_length=1, max_length=4)
    preferences: TravelPreference = Field(default_factory=TravelPreference)
    timezone: str = Field(min_length=1, max_length=64)


class TripPatch(Body):
    origin: LocationRef | None = None
    destination: LocationRef | None = None
    departure_time: datetime | None = None
    return_time: datetime | None = None
    travel_modes: list[TravelMode] | None = Field(default=None, min_length=1, max_length=4)
    preferences: TravelPreference | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    status: Literal["ACTIVE", "COMPLETED", "ARCHIVED"] | None = None
    risk_acknowledged: bool | None = None


class AssessmentCreate(Body):
    question: str | None = Field(default=None, max_length=2000)
    intent_hint: Intent | None = None

    @field_validator("question")
    @classmethod
    def _q(cls, v: str | None) -> str | None:
        return clean_text(v)


class ApplyRoute(Body):
    route_id: UUID
    recommendation_id: UUID
    acknowledge_risk: bool = False


class CancelBody(Body):
    reason: str = Field(default="user_cancelled", max_length=120)


class MessageCreate(Body):
    text: str = Field(min_length=1, max_length=2000)
    intent_hint: Intent | None = None

    @field_validator("text")
    @classmethod
    def _t(cls, v: str) -> str:
        out = clean_text(v)
        if not out:
            raise ValueError("text is empty")
        return out


class ConversationCreate(Body):
    trip_id: UUID | None = None
    title: str | None = Field(default=None, max_length=160)


class FeedbackCreate(Body):
    recommendation_id: UUID
    category: FeedbackCategory
    text: str | None = Field(default=None, max_length=4000)

    @field_validator("text")
    @classmethod
    def _t(cls, v: str | None) -> str | None:
        return clean_text(v)


class SubscriptionCreate(Body):
    trip_id: UUID
    channel: DeliveryChannel
    consent_id: UUID
    severity_threshold: Severity = Severity.MODERATE
    push_subscription: dict[str, Any] | None = None
    email: str | None = Field(default=None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class DataJobCreate(Body):
    kind: Literal["EXPORT", "DELETE"]
