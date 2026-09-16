"""decision schema baseline: policy_versions, prompt_versions, audit_traces

Revision ID: 0001_decision_baseline
Revises: None
Create Date: 2026-09-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_decision_baseline"
down_revision = None
branch_labels = None
depends_on = None
S = "decision"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {S}")
    op.create_table(
        "policy_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.String(16), nullable=False, unique=True),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("approved_by", sa.String(64)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=S,
    )
    op.create_table(
        "prompt_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.String(16), nullable=False, unique=True),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("model", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=S,
    )
    op.create_table(
        "audit_traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_hashes", postgresql.JSONB(), nullable=False),
        sa.Column("rules_fired", postgresql.JSONB(), nullable=False),
        sa.Column("locked_action", sa.String(16), nullable=False),
        sa.Column("llm_output_hash", sa.String(64)),
        sa.Column("validation_json", postgresql.JSONB(), nullable=False),
        sa.Column("versions_json", postgresql.JSONB(), nullable=False),
        sa.Column("trace_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=S,
    )
    op.create_index("ix_decision_audit_request", "audit_traces", ["request_id"], schema=S)


def downgrade() -> None:
    for t in ("audit_traces", "prompt_versions", "policy_versions"):
        op.drop_table(t, schema=S)
