"""agent schema baseline: runs, tool_calls (LangGraph checkpointer tables are created by AsyncPostgresSaver.setup())

Revision ID: 0001_agent_baseline
Revises: None
Create Date: 2026-09-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_agent_baseline"
down_revision = None
branch_labels = None
depends_on = None
S = "agent"


def upgrade() -> None:
    op.execute(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = \'{S}\') THEN EXECUTE \'CREATE SCHEMA {S}\'; END IF; END $$;")  # schema is pre-created by infra/postgres/init with the service role as owner
    op.create_table(
        "runs",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("graph_version", sa.String(48), nullable=False),
        sa.Column("prompt_version", sa.String(16)),
        sa.Column("policy_version", sa.String(16)),
        sa.Column("budgets_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("final_state", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("run_state_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=S,
    )
    op.create_index("ix_agent_runs_conversation", "runs", ["conversation_id"], schema=S)
    op.create_index("ix_agent_runs_trip", "runs", ["trip_id"], schema=S)
    op.create_table(
        "tool_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("error_code", sa.String(48)),
        sa.Column("seq", sa.Integer(), nullable=False),
        schema=S,
    )
    op.create_index("ix_agent_tool_calls_request", "tool_calls", ["request_id"], schema=S)


def downgrade() -> None:
    op.drop_table("tool_calls", schema=S)
    op.drop_table("runs", schema=S)
