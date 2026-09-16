"""Canonical entity models (00_API_AND_DATA_CONTRACTS §3).

These are the single source of truth. ``scripts/generate.py`` exports JSON Schema
and TypeScript from them. Do not hand-write parallel copies in services.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sta_contracts.enums import (
    CONTRACT_VERSION,
    ActionCode,
    ConsentType,
    DataStatus,
    DeliveryChannel,
    DisasterEventType,
    FeedbackCategory,
    Intent,
    QualityFlag,
    QualityGate,
    ReasonCode,
    RiskLevel,
    RouteLabel,
    RunStage,
    RunStatus,
    Severity,
    SourceAuthority,
    TransportServiceStatus,
    TravelMode,
)
from sta_contracts.geo import BBox, Geometry, LineString, Point, Polygon

SemVer = Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
CountryCode = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
UnitInterval = Annotated[float, Field(ge=0, le=1)]
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, ser_json_timedelta="iso8601", use_enum_values=False
    )


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        raise ValueError("timestamps must carry a timezone offset")
    return dt


# --------------------------------------------------------------------------- 3.1 LocationRef
class LocationRef(ContractModel):
    place_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)
    coordinates: Point
    country_code: CountryCode
    admin1: str | None = Field(default=None, max_length=128)
    timezone: str = Field(min_length=1, max_length=64)
    provider: str = Field(min_length=1, max_length=64)
    confirmed_by_user: bool = False


class TravelPreference(ContractModel):
    prefer_safer_route: bool = True
    prefer_lower_cost: bool = False
    prefer_lower_emissions: bool = False
    max_extra_duration_minutes: int = Field(default=90, ge=0, le=1440)
    avoid_tolls: bool = False
    accessibility: list[str] = Field(default_factory=list, max_length=10)


# --------------------------------------------------------------------------- 3.2 TravelRequest
class TravelRequest(ContractModel):
    request_id: UUID
    trip_id: UUID
    conversation_id: UUID | None = None
    user_scope_hash: str | None = Field(default=None, description="Pseudonymous owner hash; never the subject id")
    origin: LocationRef
    destination: LocationRef
    departure_time: datetime
    return_time: datetime | None = None
    travel_modes: list[TravelMode] = Field(min_length=1, max_length=4)
    preferences: TravelPreference = Field(default_factory=TravelPreference)
    question: str | None = Field(default=None, max_length=2000)
    intent_hint: Intent | None = None
    locale: str = Field(default="en-US", pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    timezone: str
    live_location_consent_id: UUID | None = None
    avoid_geometries: list[Polygon] = Field(default_factory=list, max_length=5)
    supersedes_request_id: UUID | None = None
    trip_revision: int = Field(default=1, ge=1)

    @field_validator("departure_time", "return_time")
    @classmethod
    def _tz(cls, v: datetime | None) -> datetime | None:
        return _aware(v)

    @field_validator("question")
    @classmethod
    def _normalize_question(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = unicodedata.normalize("NFC", _CONTROL.sub("", v)).strip()
        return v or None

    @model_validator(mode="after")
    def _times(self) -> TravelRequest:
        if self.return_time is not None and self.return_time <= self.departure_time:
            raise ValueError("return_time must be after departure_time")
        return self


# --------------------------------------------------------------------------- 3.3 / 3.4
class SourceProvenance(ContractModel):
    source_id: UUID
    provider: str
    provider_record_id: str | None = None
    authority: SourceAuthority = SourceAuthority.UNKNOWN
    source_url: str | None = None
    license: str | None = None
    attribution: str | None = None
    observed_at: datetime | None = None
    published_at: datetime | None = None
    fetched_at: datetime
    expires_at: datetime | None = None
    content_hash: Sha256
    schema_version: SemVer = CONTRACT_VERSION


class DataConflict(ContractModel):
    field_path: str
    values: list[Any]
    source_ids: list[UUID]
    resolution: str | None = None


class DataQuality(ContractModel):
    status: DataStatus = DataStatus.FRESH
    score: UnitInterval = 1.0
    flags: list[QualityFlag] = Field(default_factory=list)
    coverage: UnitInterval | None = None
    completeness: UnitInterval | None = None
    freshness_seconds: int | None = Field(default=None, ge=0)
    conflicts: list[DataConflict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    weights_version: SemVer = "1.0.0"


# --------------------------------------------------------------------------- 3.5 Weather
class WeatherForecastPoint(ContractModel):
    id: UUID
    location: Point
    valid_at: datetime
    route_sample_index: int | None = None
    eta_at: datetime | None = None
    temperature_c: float | None = None
    apparent_temperature_c: float | None = None
    precipitation_mm: float | None = None
    precipitation_probability: float | None = Field(default=None, ge=0, le=100)
    snowfall_cm: float | None = None
    wind_speed_kmh: float | None = None
    wind_gust_kmh: float | None = None
    visibility_m: float | None = None
    weather_code: int | None = None
    weather_category: str | None = None
    severity: Severity = Severity.UNKNOWN
    is_current: bool = False
    quality: DataQuality = Field(default_factory=DataQuality)
    source: SourceProvenance


# --------------------------------------------------------------------------- 3.6 Transport
class TransportStatus(ContractModel):
    id: UUID
    mode: TravelMode
    operator: str | None = None
    service_number: str | None = None
    origin_stop: str | None = None
    destination_stop: str | None = None
    scheduled_departure: datetime | None = None
    estimated_departure: datetime | None = None
    scheduled_arrival: datetime | None = None
    estimated_arrival: datetime | None = None
    status: TransportServiceStatus = TransportServiceStatus.UNKNOWN
    delay_minutes: int | None = None
    cancellation: bool = False
    message: str | None = None
    quality: DataQuality = Field(default_factory=DataQuality)
    source: SourceProvenance


# --------------------------------------------------------------------------- 3.7 Disaster / alert
class DisasterEvent(ContractModel):
    event_id: str = Field(min_length=1, max_length=160)
    event_type: DisasterEventType
    title: str = Field(max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    severity: Severity = Severity.UNKNOWN
    magnitude: float | None = None
    geometry: Geometry
    effective_at: datetime | None = None
    ends_at: datetime | None = None
    updated_at: datetime | None = None
    instruction: str | None = Field(default=None, max_length=2000)
    official: bool = False
    closure: bool = Field(default=False, description="Official closure / no-go; hard constraint for routes")
    country_codes: list[CountryCode] = Field(default_factory=list)
    quality: DataQuality = Field(default_factory=DataQuality)
    source: SourceProvenance


OfficialAlert = DisasterEvent


# --------------------------------------------------------------------------- 3.8 Route
class RouteSegment(ContractModel):
    index: int
    mode: TravelMode
    geometry: LineString
    distance_m: float = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    eta_start: datetime | None = None
    eta_end: datetime | None = None
    instruction: str | None = None
    operator: str | None = None
    service_number: str | None = None


class RouteExposure(ContractModel):
    score: UnitInterval = 0.0
    hazard_event_ids: list[str] = Field(default_factory=list)
    weather_window_ids: list[UUID] = Field(default_factory=list)
    closed: bool = False
    severe_weather_minutes: float = 0.0
    max_hazard_severity: Severity = Severity.UNKNOWN
    min_hazard_distance_km: float | None = None


class RouteCandidate(ContractModel):
    route_id: UUID
    provider_route_id: str | None = None
    label: RouteLabel = RouteLabel.ALTERNATIVE
    labels: list[RouteLabel] = Field(default_factory=list, description="A route may honestly hold several labels")
    mode: TravelMode
    geometry: LineString
    segments: list[RouteSegment] = Field(default_factory=list)
    distance_m: float = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    transfers: int = Field(default=0, ge=0)
    exposure: RouteExposure = Field(default_factory=RouteExposure)
    risk_level: RiskLevel = RiskLevel.UNKNOWN
    risk_score: UnitInterval | None = None
    rank: int | None = None
    usable: bool = True
    trade_offs: list[str] = Field(default_factory=list)
    quality: DataQuality = Field(default_factory=DataQuality)
    sources: list[SourceProvenance] = Field(default_factory=list)


# --------------------------------------------------------------------------- 3.9 Snapshot
class TravelWindow(ContractModel):
    departure_at: datetime
    arrival_at: datetime
    timezone: str

    @model_validator(mode="after")
    def _order(self) -> TravelWindow:
        if self.arrival_at < self.departure_at:
            raise ValueError("arrival_at before departure_at")
        return self


class QualitySummary(ContractModel):
    gate: QualityGate
    overall: DataQuality
    weather: DataQuality
    transport: DataQuality
    disaster: DataQuality
    route: DataQuality
    degraded_services: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class ConflictSummary(ContractModel):
    count: int = 0
    safety_critical: int = 0
    conflicts: list[DataConflict] = Field(default_factory=list)


class IntegratedTravelContext(ContractModel):
    snapshot_id: UUID
    request_id: UUID
    trip_id: UUID
    trip_revision: int = 1
    schema_version: SemVer = CONTRACT_VERSION
    feature_schema_version: SemVer = "1.0.0"
    transform_version: SemVer = "1.0.0"
    travel_window: TravelWindow
    travel_modes: list[TravelMode]
    route_candidates: list[RouteCandidate]
    route_corridor_geojson: Polygon | None = None
    corridor_buffer_m: float | None = None
    weather: list[WeatherForecastPoint]
    transport: list[TransportStatus]
    disaster_events: list[DisasterEvent]
    official_alerts: list[DisasterEvent]
    features: dict[str, float | int | None]
    quality_summary: QualitySummary
    conflict_summary: ConflictSummary
    source_ids: list[UUID]
    provider_health: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    content_hash: Sha256
    supersedes_snapshot_id: UUID | None = None


# --------------------------------------------------------------------------- 3.10 Risk
class ModelRef(ContractModel):
    name: str
    version: str
    feature_schema_version: SemVer
    thresholds_version: SemVer = "1.0.0"
    checksum: str | None = None


class SafetyOverride(ContractModel):
    code: ReasonCode
    minimum_risk: RiskLevel
    event_ids: list[str] = Field(default_factory=list)
    policy_version: SemVer = "1.0.0"


class RiskAssessment(ContractModel):
    assessment_id: UUID
    snapshot_id: UUID
    route_id: UUID
    score: UnitInterval
    probability_high: UnitInterval
    risk_level: RiskLevel
    uncertainty: UnitInterval
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    safety_overrides: list[SafetyOverride] = Field(default_factory=list)
    feature_contributions: dict[str, float] = Field(default_factory=dict)
    time_dependent: bool = False
    later_window_risk_level: RiskLevel | None = None
    later_window_delay_minutes: int | None = None
    model: ModelRef
    quality: DataQuality = Field(default_factory=DataQuality)
    created_at: datetime


# --------------------------------------------------------------------------- 3.11 Evidence
class RetrievedEvidence(ContractModel):
    evidence_id: UUID
    document_id: str
    authority: SourceAuthority
    title: str
    source_url: str
    page: int | None = None
    section: str | None = None
    language: str
    hazards: list[DisasterEventType] = Field(default_factory=list)
    effective_at: datetime | None = None
    expires_at: datetime | None = None
    passage: str = Field(max_length=4000)
    retrieval_score: float
    rerank_score: float | None = None
    collection_version: str
    content_hash: Sha256


# --------------------------------------------------------------------------- Decision
class EvidencePackage(ContractModel):
    package_id: UUID
    request_id: UUID
    snapshot_id: UUID
    trip_revision: int = 1
    risk_assessments: list[RiskAssessment]
    routes: list[RouteCandidate]
    evidence: list[RetrievedEvidence]
    quality_summary: QualitySummary
    conflict_summary: ConflictSummary
    official_alerts: list[DisasterEvent]
    weather_facts: list[str] = Field(default_factory=list)
    transport_facts: list[str] = Field(default_factory=list)
    disaster_facts: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    degraded_services: list[str] = Field(default_factory=list)
    knowledge_collection_version: str | None = None
    created_at: datetime


class VersionInfo(ContractModel):
    policy: str | None = None
    prompt: str | None = None
    llm_model: str | None = None
    contract: SemVer = CONTRACT_VERSION
    graph: str | None = None
    model: str | None = None
    feature_schema: str | None = None
    knowledge_collection: str | None = None


class DecisionValidation(ContractModel):
    """Wire field ``schema`` is exposed via alias because ``schema`` shadows a BaseModel attribute."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

    schema_valid: bool = Field(default=True, alias="schema")
    citations: bool = True
    locked_action: bool = True
    numbers: bool = True
    banned_phrases: bool = True
    used_fallback: bool = False
    fallback_reason: str | None = None


class DecisionResult(ContractModel):
    decision_id: UUID
    request_id: UUID
    snapshot_id: UUID
    action_code: ActionCode
    risk_level: RiskLevel
    confidence: UnitInterval
    selected_route_id: UUID | None = None
    rules_fired: list[str] = Field(default_factory=list)
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    escalation_required: bool = False
    escalation_reasons: list[str] = Field(default_factory=list)
    summary: str = Field(max_length=600)
    reasons: list[str] = Field(default_factory=list, max_length=8)
    immediate_actions: list[str] = Field(default_factory=list, max_length=8)
    citations: list[UUID] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list, max_length=12)
    suggested_delay_minutes: int | None = None
    versions: VersionInfo
    validation: DecisionValidation = Field(default_factory=DecisionValidation)
    locale: str = "en-US"
    created_at: datetime


# --------------------------------------------------------------------------- Recommendation
class OfficialContact(ContractModel):
    country_code: CountryCode
    subdivision: str | None = None
    service_type: Literal["POLICE", "MEDICAL", "FIRE", "TOURIST_POLICE", "GENERAL_EMERGENCY", "EMBASSY", "DISASTER"]
    label: str
    phone: str
    phone_e164: str | None = None
    website: str | None = None
    availability: str | None = None
    source_url: str
    authority: SourceAuthority
    effective_at: datetime
    verified_at: datetime
    review_due_at: datetime | None = None


class AlertItem(ContractModel):
    alert_id: str
    title: str
    severity: Severity
    event_type: DisasterEventType
    official: bool
    area: str | None = None
    effective_at: datetime | None = None
    ends_at: datetime | None = None
    source_url: str | None = None
    observed_at: datetime | None = None
    fetched_at: datetime | None = None


class Freshness(ContractModel):
    observed_at: datetime | None = None
    fetched_at: datetime
    expires_at: datetime | None = None


class EmergencyInstruction(ContractModel):
    step: int
    text: str
    citation_id: UUID | None = None


class RecommendationResponse(ContractModel):
    recommendation_id: UUID
    request_id: UUID
    trip_id: UUID
    conversation_id: UUID | None = None
    decision_id: UUID
    snapshot_id: UUID
    status: RunStatus
    action_code: ActionCode
    risk_level: RiskLevel
    confidence: UnitInterval
    short_summary: str = Field(max_length=600)
    immediate_actions: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    primary_route: RouteCandidate | None = None
    alternatives: list[RouteCandidate] = Field(default_factory=list)
    alerts: list[AlertItem] = Field(default_factory=list)
    emergency_instructions: list[EmergencyInstruction] = Field(default_factory=list)
    official_contacts: list[OfficialContact] = Field(default_factory=list)
    sources: list[SourceProvenance] = Field(default_factory=list)
    evidence: list[RetrievedEvidence] = Field(default_factory=list)
    weather_summary: dict[str, Any] | None = None
    transport_summary: dict[str, Any] | None = None
    freshness: Freshness
    limitations: list[str] = Field(default_factory=list)
    degraded_services: list[str] = Field(default_factory=list)
    escalation_required: bool = False
    versions: VersionInfo
    locale: str = "en-US"
    expires_at: datetime | None = None
    supersedes_recommendation_id: UUID | None = None
    created_at: datetime


# --------------------------------------------------------------------------- Runs / SSE
class RunRef(ContractModel):
    request_id: UUID
    status: RunStatus
    events_url: str
    poll_url: str
    submitted_at: datetime
    recommendation_id: UUID | None = None
    conversation_id: UUID | None = None


class RunProgressEvent(ContractModel):
    """Payload of an SSE event. No prompts, raw provider bodies, coordinates or PII."""

    event_id: int = Field(ge=0)
    event_type: Literal[
        "run.accepted", "run.progress", "run.needs_input", "run.degraded", "run.completed", "run.failed", "heartbeat"
    ]
    request_id: UUID
    status: RunStatus | None = None
    stage: RunStage | None = None
    percent: int | None = Field(default=None, ge=0, le=100)
    message_key: str | None = None
    service: str | None = None
    reason: str | None = None
    retrying: bool | None = None
    missing_fields: list[str] | None = None
    prompt_key: str | None = None
    recommendation_id: UUID | None = None
    result_url: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool | None = None
    occurred_at: datetime


class RunState(ContractModel):
    request_id: UUID
    trip_id: UUID
    conversation_id: UUID | None = None
    status: RunStatus
    stage: RunStage | None = None
    intent: Intent | None = None
    missing_fields: list[str] = Field(default_factory=list)
    recommendation_id: UUID | None = None
    snapshot_id: UUID | None = None
    decision_id: UUID | None = None
    degraded_services: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    step_count: int = 0
    tool_call_count: int = 0
    llm_call_count: int = 0
    versions: VersionInfo
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


# --------------------------------------------------------------------------- Users / trips / feedback
class ConsentRecord(ContractModel):
    id: UUID
    type: ConsentType
    granted: bool
    policy_version: str
    granted_at: datetime | None = None
    revoked_at: datetime | None = None
    expires_at: datetime | None = None


class UserProfile(ContractModel):
    id: UUID
    locale: str
    timezone: str
    display_name: str | None = None
    consents: list[ConsentRecord] = Field(default_factory=list)
    has_emergency_profile: bool = False
    created_at: datetime
    updated_at: datetime


class EmergencyContactPerson(ContractModel):
    name: str = Field(max_length=120)
    relationship: str | None = Field(default=None, max_length=60)
    phone: str = Field(max_length=32)


class EmergencyProfile(ContractModel):
    blood_type: str | None = Field(default=None, max_length=8)
    allergies: list[str] = Field(default_factory=list, max_length=20)
    medications: list[str] = Field(default_factory=list, max_length=20)
    medical_notes: str | None = Field(default=None, max_length=2000)
    contacts: list[EmergencyContactPerson] = Field(default_factory=list, max_length=5)
    insurance_provider: str | None = Field(default=None, max_length=120)
    insurance_policy_ref: str | None = Field(default=None, max_length=120)
    insurance_phone: str | None = Field(default=None, max_length=32)
    updated_at: datetime | None = None


class Trip(ContractModel):
    id: UUID
    revision: int
    origin: LocationRef
    destination: LocationRef
    departure_time: datetime
    return_time: datetime | None = None
    travel_modes: list[TravelMode]
    preferences: TravelPreference
    timezone: str
    status: Literal["DRAFT", "ACTIVE", "COMPLETED", "ARCHIVED", "DELETED"] = "ACTIVE"
    selected_route_id: UUID | None = None
    previous_route_id: UUID | None = None
    latest_request_id: UUID | None = None
    latest_recommendation_id: UUID | None = None
    risk_acknowledged_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class FeedbackEvent(ContractModel):
    id: UUID
    recommendation_id: UUID
    category: FeedbackCategory
    text_redacted: str | None = None
    review_status: Literal["NONE", "QUEUED", "IN_REVIEW", "RESOLVED"] = "NONE"
    safety_review_id: UUID | None = None
    created_at: datetime


class AlertSubscription(ContractModel):
    id: UUID
    trip_id: UUID
    channel: DeliveryChannel
    consent_id: UUID
    status: Literal["ACTIVE", "PAUSED", "REVOKED"]
    severity_threshold: Severity = Severity.MODERATE
    locale: str = "en-US"
    cooldown_until: datetime | None = None
    created_at: datetime
    revoked_at: datetime | None = None


class Conversation(ContractModel):
    id: UUID
    trip_id: UUID | None = None
    title: str
    last_request_id: UUID | None = None
    message_count: int = 0
    created_at: datetime
    updated_at: datetime


class ConversationMessage(ContractModel):
    id: UUID
    conversation_id: UUID
    role: Literal["user", "assistant", "system"]
    text: str | None = None
    request_id: UUID | None = None
    recommendation_id: UUID | None = None
    created_at: datetime


# --------------------------------------------------------------------------- Internal query models
class TimeWindow(ContractModel):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _order(self) -> TimeWindow:
        if self.end < self.start:
            raise ValueError("end before start")
        return self


ContextInclude = Literal["weather", "routes", "transport", "disasters"]
ALL_CONTEXT_INCLUDES: tuple[ContextInclude, ...] = ("weather", "routes", "transport", "disasters")


class ContextQuery(ContractModel):
    """Input to external-data /internal/v1/context/query."""

    request_id: UUID
    trip_id: UUID
    origin: LocationRef
    destination: LocationRef
    departure_time: datetime
    travel_modes: list[TravelMode]
    preferences: TravelPreference = Field(default_factory=TravelPreference)
    locale: str = "en-US"
    avoid_geometries: list[Polygon] = Field(default_factory=list)
    max_weather_samples: int = Field(default=12, ge=2, le=40)
    include: list[ContextInclude] = Field(default_factory=lambda: list(ALL_CONTEXT_INCLUDES))


class ProviderHealth(ContractModel):
    provider: str
    kind: str
    status: Literal["UP", "DEGRADED", "DOWN", "UNAVAILABLE", "UNKNOWN"]
    enabled: bool
    latency_ms: float | None = None
    quota_remaining: int | None = None
    coverage_note: str | None = None
    checked_at: datetime
    last_error_code: str | None = None


class ExternalContext(ContractModel):
    """Output of external-data /internal/v1/context/query — all canonical records + provider health."""

    context_id: UUID
    request_id: UUID
    routes: list[RouteCandidate]
    weather: list[WeatherForecastPoint]
    transport: list[TransportStatus]
    disaster_events: list[DisasterEvent]
    official_alerts: list[DisasterEvent]
    provider_health: list[ProviderHealth]
    degraded_services: list[str] = Field(default_factory=list)
    unavailable_capabilities: list[str] = Field(default_factory=list)
    fetched_at: datetime
    bbox: BBox | None = None


class SnapshotCreateRequest(ContractModel):
    travel_request: TravelRequest
    external_context: ExternalContext
    corridor_buffer_m: float | None = None


class EvidencePackageRequest(ContractModel):
    snapshot: IntegratedTravelContext
    locale: str = "en-US"
    question: str | None = None


class DecisionRequest(ContractModel):
    travel_request: TravelRequest
    evidence_package: EvidencePackage
    locale: str = "en-US"
    llm_enabled: bool = True


class RecommendationCreateRequest(ContractModel):
    travel_request: TravelRequest
    decision: DecisionResult
    evidence_package: EvidencePackage
    snapshot_created_at: datetime
    snapshot_sources: list[SourceProvenance]
    weather_summary: dict[str, Any] | None = None
    transport_summary: dict[str, Any] | None = None
    previous_recommendation_id: UUID | None = None


class AgentRunRequest(ContractModel):
    travel_request: TravelRequest
    conversation_id: UUID | None = None
    resume_from_request_id: UUID | None = None
    budget_override: dict[str, int] | None = None


__all__ = [name for name in dir() if name[:1].isupper()]
