"""recommendation schema baseline

Revision ID: 0001_recommendation_baseline
Revises: None
Create Date: 2026-09-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_recommendation_baseline"
down_revision = None
branch_labels = None
depends_on = None
S = "recommendation"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {S}")
    op.create_table(
        "recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_pseudonym", sa.String(64)),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("risk", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("response_json", postgresql.JSONB(), nullable=False),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("request_id", "decision_id", name="uq_recommendation_request_decision"),
        schema=S,
    )
    op.create_index("ix_recommendation_trip", "recommendations", ["trip_id", "created_at"], schema=S)
    op.create_table(
        "feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_pseudonym", sa.String(64), nullable=False),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("text_redacted", sa.Text()),
        sa.Column("review_status", sa.String(16), nullable=False, server_default="NONE"),
        sa.Column("safety_review_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        schema=S,
    )
    op.create_index("ix_recommendation_feedback_rec", "feedback", ["recommendation_id"], schema=S)
    op.create_table(
        "safety_review_queue",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("feedback_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("assigned_to", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=S,
    )
    op.create_table(
        "subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_pseudonym", sa.String(64), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(8), nullable=False),
        sa.Column("consent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default="ACTIVE"),
        sa.Column("severity_threshold", sa.String(8), nullable=False, server_default="MODERATE"),
        sa.Column("locale", sa.String(8), nullable=False, server_default="en-US"),
        sa.Column("target_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("cooldown_until", sa.DateTime(timezone=True)),
        sa.Column("last_recommendation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        schema=S,
    )
    op.create_index("ix_recommendation_subs_trip", "subscriptions", ["trip_id", "status"], schema=S)
    op.create_table(
        "delivery_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provider_message_id", sa.String(128)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("subscription_id", "event_hash", "channel", name="uq_recommendation_delivery_event"),
        schema=S,
    )
    op.create_table(
        "emergency_contacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("subdivision", sa.String(64)),
        sa.Column("service_type", sa.String(24), nullable=False),
        sa.Column("labels_json", postgresql.JSONB(), nullable=False),
        sa.Column("phone", sa.String(32), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("authority", sa.String(24), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("directory_version", sa.String(24), nullable=False),
        sa.UniqueConstraint("checksum", name="uq_recommendation_contact_checksum"),
        schema=S,
    )


def downgrade() -> None:
    for t in ("emergency_contacts", "delivery_log", "subscriptions", "safety_review_queue", "feedback", "recommendations"):
        op.drop_table(t, schema=S)
