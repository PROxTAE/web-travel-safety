"""identity + travel baseline: user_profiles, consents, emergency_profiles, audit_log, data_jobs, trips, requests,
conversations, conversation_messages

Revision ID: 0001_identity_travel_baseline
Revises: None
Create Date: 2026-09-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_identity_travel_baseline"
down_revision = None
branch_labels = None
depends_on = None
I = "identity"
T = "travel"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {I}")
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {T}")
    op.create_table(
        "user_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subject_id", sa.String(255), nullable=False, unique=True),
        sa.Column("locale", sa.String(8), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        schema=I,
    )
    op.create_table(
        "consents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey(f"{I}.user_profiles.id"), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=I,
    )
    op.create_index("ix_identity_consents_user_type", "consents", ["user_id", "type"], schema=I)
    op.create_table(
        "emergency_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey(f"{I}.user_profiles.id"), nullable=False, unique=True),
        sa.Column("encrypted_payload", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=I,
    )
    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.String(64)),
        sa.Column("meta_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=I,
    )
    op.create_index("ix_identity_audit_user_created", "audit_log", ["user_id", "created_at"], schema=I)
    op.create_table(
        "data_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("result_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        schema=I,
    )
    op.create_index("ix_identity_data_jobs_user", "data_jobs", ["user_id"], schema=I)

    op.create_table(
        "trips",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("origin_json", postgresql.JSONB(), nullable=False),
        sa.Column("destination_json", postgresql.JSONB(), nullable=False),
        sa.Column("departure_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("return_time", sa.DateTime(timezone=True)),
        sa.Column("modes", postgresql.JSONB(), nullable=False),
        sa.Column("preferences_json", postgresql.JSONB(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("selected_route_id", postgresql.UUID(as_uuid=True)),
        sa.Column("previous_route_id", postgresql.UUID(as_uuid=True)),
        sa.Column("latest_request_id", postgresql.UUID(as_uuid=True)),
        sa.Column("latest_recommendation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("risk_acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        schema=T,
    )
    op.create_index("ix_travel_trips_user_updated", "trips", ["user_id", "updated_at"], schema=T)
    op.create_table(
        "requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey(f"{T}.trips.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64)),
        sa.Column("fingerprint", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("contract_version", sa.String(16), nullable=False),
        sa.Column("trip_revision", sa.Integer(), nullable=False),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("previous_recommendation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("error_code", sa.String(48)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        schema=T,
    )
    op.create_index("ix_travel_requests_trip", "requests", ["trip_id"], schema=T)
    op.create_index("ix_travel_requests_user_created", "requests", ["user_id", "created_at"], schema=T)
    op.create_index(
        "ux_travel_requests_idempotency",
        "requests",
        ["user_id", "idempotency_key_hash"],
        unique=True,
        schema=T,
        postgresql_where=sa.text("idempotency_key_hash IS NOT NULL"),
    )
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True)),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("last_request_id", postgresql.UUID(as_uuid=True)),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=T,
    )
    op.create_index("ix_travel_conversations_user_updated", "conversations", ["user_id", "updated_at"], schema=T)
    op.create_table(
        "conversation_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey(f"{T}.conversations.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("text", sa.Text()),
        sa.Column("request_id", postgresql.UUID(as_uuid=True)),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=T,
    )
    op.create_index("ix_travel_messages_conversation", "conversation_messages", ["conversation_id", "created_at"], schema=T)


def downgrade() -> None:
    for name in ("conversation_messages", "conversations", "requests", "trips"):
        op.drop_table(name, schema=T)
    for name in ("data_jobs", "audit_log", "emergency_profiles", "consents", "user_profiles"):
        op.drop_table(name, schema=I)
