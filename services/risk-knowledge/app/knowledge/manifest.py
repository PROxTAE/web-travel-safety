"""Approved source manifest (knowledge/sources.yaml) + validation."""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator
from sta_contracts.enums import DisasterEventType, SourceAuthority

APPROVED_AUTHORITIES = {SourceAuthority.OFFICIAL, SourceAuthority.INTERGOVERNMENTAL}


class SourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(pattern=r"^[a-z0-9\-]+$")
    title: str
    authority: SourceAuthority
    organization: str
    source_url: HttpUrl
    country: str = "GLOBAL"
    hazards: list[DisasterEventType]
    languages: list[str]
    effective_at: date
    expires_at: date
    review_due_at: date
    license: str
    ingestion_method: str = "html"
    reviewer: str
    review_status: str
    checksum: str | None = None

    @field_validator("review_status")
    @classmethod
    def _approved(cls, v: str) -> str:
        if v != "APPROVED":
            raise ValueError("only APPROVED sources may be ingested")
        return v

    @property
    def effective_dt(self) -> datetime:
        return datetime.combine(self.effective_at, time.min).astimezone()

    @property
    def expires_dt(self) -> datetime:
        return datetime.combine(self.expires_at, time.min).astimezone()


class SourceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collection_version: str
    allowed_domains: list[str]
    documents: list[SourceDocument]

    @classmethod
    def load(cls, path: str | Path) -> SourceManifest:
        m = cls.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
        for d in m.documents:
            host = urlsplit(str(d.source_url)).netloc
            if host not in m.allowed_domains:
                raise ValueError(f"{d.document_id}: host {host} not in allowed_domains")
            if d.authority not in APPROVED_AUTHORITIES:
                raise ValueError(f"{d.document_id}: authority {d.authority} not approved for knowledge base")
            if d.expires_at <= d.effective_at:
                raise ValueError(f"{d.document_id}: expires_at must be after effective_at")
        return m
