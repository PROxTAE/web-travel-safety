"""Verified emergency directory: manifest loading, validation, expiry guard, resolver by country/subdivision/type."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Literal

import phonenumbers
import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from sta_contracts.enums import SourceAuthority
from sta_contracts.models import OfficialContact

ServiceType = Literal["POLICE", "MEDICAL", "FIRE", "TOURIST_POLICE", "GENERAL_EMERGENCY", "EMBASSY", "DISASTER"]


class DirectoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country_code: str = Field(pattern=r"^[A-Z]{2}$")
    subdivision: str | None = None
    service_type: ServiceType
    label_i18n: dict[str, str]
    phone: str = Field(min_length=2, max_length=32)
    sms: str | None = None
    website: HttpUrl | None = None
    availability: str | None = None
    source_url: HttpUrl
    authority: SourceAuthority
    organization: str
    effective_at: date
    verified_at: date
    review_due_at: date
    reviewer: str
    review_status: Literal["VERIFIED", "PENDING_VERIFICATION", "EXPIRED", "RETIRED"]

    @property
    def checksum(self) -> str:
        return hashlib.sha256(
            f"{self.country_code}|{self.subdivision}|{self.service_type}|{self.phone}|{self.source_url}".encode()
        ).hexdigest()

    def phone_e164(self) -> str | None:
        """E.164 only when the number is a full national number; short codes (191, 1669) stay local."""
        try:
            parsed = phonenumbers.parse(self.phone, self.country_code)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            return None
        return None


class Directory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    directory_version: str
    records: list[DirectoryRecord]

    @classmethod
    def load(cls, path: str | Path) -> Directory:
        d = cls.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
        for r in d.records:
            if r.authority not in (SourceAuthority.OFFICIAL, SourceAuthority.INTERGOVERNMENTAL):
                raise ValueError(f"{r.country_code}/{r.service_type}: non-official authority not allowed in directory")
            if r.review_due_at <= r.verified_at:
                raise ValueError(f"{r.country_code}/{r.service_type}: review_due_at must be after verified_at")
        return d

    def resolve(
        self,
        country_code: str,
        *,
        subdivision: str | None = None,
        service_types: list[str] | None = None,
        locale: str = "en",
        now: datetime | None = None,
    ) -> tuple[list[OfficialContact], list[str]]:
        """Return (contacts, limitations). Expired / unverified records are excluded and reported."""
        now = now or datetime.now(UTC)
        today = now.date()
        lang = locale.split("-")[0].lower()
        out: list[OfficialContact] = []
        limitations: list[str] = []
        matched_any = False
        for r in self.records:
            if r.country_code != country_code.upper():
                continue
            if service_types and r.service_type not in service_types:
                continue
            if r.subdivision and subdivision and r.subdivision.lower() != subdivision.lower():
                continue
            matched_any = True
            if r.review_status != "VERIFIED":
                limitations.append(f"{r.country_code}/{r.service_type}: {r.review_status}")
                continue
            if r.review_due_at < today:
                limitations.append(f"{r.country_code}/{r.service_type}: REVIEW_OVERDUE since {r.review_due_at}")
                continue
            if r.effective_at > today:
                continue
            out.append(
                OfficialContact(
                    country_code=r.country_code,
                    subdivision=r.subdivision,
                    service_type=r.service_type,
                    label=r.label_i18n.get(lang) or r.label_i18n.get("en") or next(iter(r.label_i18n.values())),
                    phone=r.phone,
                    phone_e164=r.phone_e164(),
                    website=str(r.website) if r.website else None,
                    availability=r.availability,
                    source_url=str(r.source_url),
                    authority=r.authority,
                    effective_at=datetime.combine(r.effective_at, time.min, tzinfo=UTC),
                    verified_at=datetime.combine(r.verified_at, time.min, tzinfo=UTC),
                    review_due_at=datetime.combine(r.review_due_at, time.min, tzinfo=UTC),
                )
            )
        if not matched_any:
            limitations.append(f"EMERGENCY_DIRECTORY_UNAVAILABLE:{country_code.upper()}")
        # subdivision-specific records first, then country-wide; stable order by service type
        order = {
            "GENERAL_EMERGENCY": 0,
            "POLICE": 1,
            "MEDICAL": 2,
            "FIRE": 3,
            "DISASTER": 4,
            "TOURIST_POLICE": 5,
            "EMBASSY": 6,
        }
        out.sort(key=lambda c: (order.get(c.service_type, 9), c.subdivision is None))
        return out, limitations
