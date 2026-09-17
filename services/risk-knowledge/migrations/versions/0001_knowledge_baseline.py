"""knowledge schema baseline: model_versions, documents, chunks, risk_assessments, route_evaluations

Revision ID: 0001_knowledge_baseline
Revises: None
Create Date: 2026-09-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_knowledge_baseline"
down_revision = None
branch_labels = None
depends_on = None
S = "knowledge"


def upgrade() -> None:
    op.execute(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = \'{S}\') THEN EXECUTE \'CREATE SCHEMA {S}\'; END IF; END $$;")  # schema is pre-created by infra/postgres/init with the service role as owner
    op.create_table(
        "model_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("feature_schema", sa.String(16), nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("artifact_uri", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("dataset_manifest_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("approved_by", sa.String(64)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("name", "version", name="uq_knowledge_model_version"),
        schema=S,
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("authority", sa.String(32), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column("country", sa.String(8), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("review_status", sa.String(16), nullable=False),
        sa.Column("collection_version", sa.String(32), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=S,
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("document_id", sa.String(64), nullable=False),
        sa.Column("section", sa.String(160)),
        sa.Column("page", sa.Integer()),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("qdrant_point_id", sa.String(64), nullable=False),
        sa.Column("collection_version", sa.String(32), nullable=False),
        schema=S,
    )
    op.create_index("ix_knowledge_chunks_doc", "chunks", ["document_id", "collection_version"], schema=S)
    op.create_table(
        "risk_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("route_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("risk_level", sa.String(8), nullable=False),
        sa.Column("model_version", sa.String(32), nullable=False),
        sa.Column("thresholds_version", sa.String(16), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=S,
    )
    op.create_index("ix_knowledge_assessments_snapshot", "risk_assessments", ["snapshot_id"], schema=S)
    op.create_table(
        "route_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ranking_version", sa.String(16), nullable=False),
        sa.Column("recommended_route_id", postgresql.UUID(as_uuid=True)),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=S,
    )


def downgrade() -> None:
    for t in ("route_evaluations", "risk_assessments", "chunks", "documents", "model_versions"):
        op.drop_table(t, schema=S)
