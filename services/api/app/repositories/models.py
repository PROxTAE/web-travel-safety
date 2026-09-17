"""SQLAlchemy tables for schemas ``identity`` and ``travel`` (00_API_AND_DATA_CONTRACTS §8, owner คน 2)."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Integer, LargeBinary, MetaData, String, Table, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

IDENTITY = "identity"
TRAVEL = "travel"
metadata = MetaData()

user_profiles = Table(
    "user_profiles",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("subject_id", String(255), nullable=False, unique=True),
    Column("locale", String(8), nullable=False),
    Column("timezone", String(64), nullable=False),
    Column("display_name", String(120)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True)),
    schema=IDENTITY,
)

consents = Table(
    "consents",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("type", String(32), nullable=False),
    Column("granted", Boolean, nullable=False),
    Column("policy_version", String(32), nullable=False),
    Column("granted_at", DateTime(timezone=True)),
    Column("revoked_at", DateTime(timezone=True)),
    Column("expires_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    schema=IDENTITY,
)

emergency_profiles = Table(
    "emergency_profiles",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), nullable=False, unique=True),
    Column("encrypted_payload", LargeBinary, nullable=False),
    Column("key_version", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    schema=IDENTITY,
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("action", String(48), nullable=False),
    Column("resource_type", String(32), nullable=False),
    Column("resource_id", String(64)),
    Column("meta_json", JSONB, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), nullable=False),
    schema=IDENTITY,
)

data_jobs = Table(
    "data_jobs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("kind", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("requested_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
    Column("result_json", JSONB, nullable=False, default=dict),
    schema=IDENTITY,
)

trips = Table(
    "trips",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("revision", Integer, nullable=False),
    Column("origin_json", JSONB, nullable=False),
    Column("destination_json", JSONB, nullable=False),
    Column("departure_time", DateTime(timezone=True), nullable=False),
    Column("return_time", DateTime(timezone=True)),
    Column("modes", JSONB, nullable=False),
    Column("preferences_json", JSONB, nullable=False),
    Column("timezone", String(64), nullable=False),
    Column("status", String(16), nullable=False),
    Column("selected_route_id", UUID(as_uuid=True)),
    Column("previous_route_id", UUID(as_uuid=True)),
    Column("latest_request_id", UUID(as_uuid=True)),
    Column("latest_recommendation_id", UUID(as_uuid=True)),
    Column("risk_acknowledged_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True)),
    schema=TRAVEL,
)

requests = Table(
    "requests",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("trip_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("user_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("conversation_id", UUID(as_uuid=True), index=True),
    Column("kind", String(16), nullable=False),
    Column("idempotency_key_hash", String(64), index=True),
    Column("fingerprint", String(64)),
    Column("status", String(16), nullable=False),
    Column("contract_version", String(16), nullable=False),
    Column("trip_revision", Integer, nullable=False),
    Column("recommendation_id", UUID(as_uuid=True)),
    Column("previous_recommendation_id", UUID(as_uuid=True)),
    Column("error_code", String(48)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
    schema=TRAVEL,
)

conversations = Table(
    "conversations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("trip_id", UUID(as_uuid=True)),
    Column("title", String(160), nullable=False),
    Column("last_request_id", UUID(as_uuid=True)),
    Column("message_count", Integer, nullable=False, default=0),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    schema=TRAVEL,
)

conversation_messages = Table(
    "conversation_messages",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("conversation_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("role", String(12), nullable=False),
    Column("text", Text),
    Column("request_id", UUID(as_uuid=True)),
    Column("recommendation_id", UUID(as_uuid=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    schema=TRAVEL,
)
