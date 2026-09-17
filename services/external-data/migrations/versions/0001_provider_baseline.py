"""provider schema baseline: providers, fetch_log, health

Revision ID: 0001_provider_baseline
Revises: None
Create Date: 2026-09-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_provider_baseline"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "provider"


def upgrade() -> None:
    op.execute(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = \'{SCHEMA}\') THEN EXECUTE \'CREATE SCHEMA {SCHEMA}\'; END IF; END $$;")  # schema is pre-created by infra/postgres/init with the service role as owner
    op.create_table(
        "providers",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("coverage_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("license_url", sa.Text()),
        sa.Column("attribution", sa.Text()),
        sa.Column("config_version", sa.String(32), nullable=False, server_default="1.0.0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_table(
        "fetch_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider_id", sa.String(64), sa.ForeignKey(f"{SCHEMA}.providers.id"), nullable=False),
        sa.Column("request_id", sa.String(64)),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(48)),
        sa.Column("record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("from_cache", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("latency_ms", sa.Float()),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("quality_json", sa.JSON(), nullable=False, server_default="{}"),
        schema=SCHEMA,
    )
    op.create_index("ix_provider_fetch_log_provider_time", "fetch_log", ["provider_id", "fetched_at"], schema=SCHEMA)
    op.create_index("ix_provider_fetch_log_query_hash", "fetch_log", ["query_hash"], schema=SCHEMA)
    op.create_table(
        "health",
        sa.Column("provider_id", sa.String(64), sa.ForeignKey(f"{SCHEMA}.providers.id"), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("latency_ms", sa.Float()),
        sa.Column("quota_remaining", sa.Integer()),
        sa.Column("last_error_code", sa.String(48)),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    # retention: fetch_log rows older than 30 days are purged by ops/scripts/retention (documented, not destructive here)


def downgrade() -> None:
    op.drop_table("health", schema=SCHEMA)
    op.drop_index("ix_provider_fetch_log_query_hash", table_name="fetch_log", schema=SCHEMA)
    op.drop_index("ix_provider_fetch_log_provider_time", table_name="fetch_log", schema=SCHEMA)
    op.drop_table("fetch_log", schema=SCHEMA)
    op.drop_table("providers", schema=SCHEMA)
