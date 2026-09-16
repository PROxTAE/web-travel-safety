"""integration schema baseline: snapshots, disaster_events, weather_records, transport_records, lineage, quarantine

Revision ID: 0001_integration_baseline
Revises: None
Create Date: 2026-09-17
"""

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_integration_baseline"
down_revision = None
branch_labels = None
depends_on = None

S = "integration"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {S}")
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table(
        "snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trip_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("input_content_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("feature_schema_version", sa.String(16), nullable=False),
        sa.Column("transform_version", sa.String(16), nullable=False),
        sa.Column("gate", sa.String(16), nullable=False),
        sa.Column("quality_json", postgresql.JSONB(), nullable=False),
        sa.Column("features_json", postgresql.JSONB(), nullable=False),
        sa.Column("snapshot_json", postgresql.JSONB(), nullable=False),
        sa.Column("corridor", geoalchemy2.Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False), nullable=True),
        sa.Column("departure_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("arrival_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("request_id", "input_content_hash", "schema_version", name="uq_integration_snapshot_input"),
        schema=S,
    )
    op.create_index("ix_integration_snapshots_request", "snapshots", ["request_id"], schema=S)
    op.create_index("ix_integration_snapshots_trip", "snapshots", ["trip_id", "created_at"], schema=S)
    op.create_index("ix_integration_snapshots_corridor", "snapshots", ["corridor"], schema=S, postgresql_using="gist")

    op.create_table(
        "disaster_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", sa.String(160), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("official", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("closure", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("geom", geoalchemy2.Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("record_json", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("event_id", "content_hash", name="uq_integration_event_hash"),
        schema=S,
    )
    op.create_index("ix_integration_events_geom", "disaster_events", ["geom"], schema=S, postgresql_using="gist")
    op.create_index("ix_integration_events_time", "disaster_events", ["effective_at", "ends_at"], schema=S)
    op.create_index("ix_integration_events_severity", "disaster_events", ["severity", "official"], schema=S)

    op.create_table(
        "weather_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("valid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("geom", geoalchemy2.Geometry(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False),
        sa.Column("record_json", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("content_hash", name="uq_integration_weather_hash"),
        schema=S,
    )
    op.create_index("ix_integration_weather_geom", "weather_records", ["geom"], schema=S, postgresql_using="gist")
    op.create_index("ix_integration_weather_valid", "weather_records", ["valid_at"], schema=S, postgresql_using="brin")

    op.create_table(
        "transport_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("operator", sa.String(128)),
        sa.Column("service_number", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_json", postgresql.JSONB(), nullable=False),
        schema=S,
    )
    op.create_index("ix_integration_transport_service", "transport_records", ["operator", "service_number", "status"], schema=S)

    op.create_table(
        "lineage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("record_type", sa.String(16), nullable=False),
        sa.Column("record_id", sa.String(160), nullable=False),
        sa.Column("field_path", sa.String(128), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("transform", sa.String(64), nullable=False),
        sa.Column("transform_version", sa.String(16), nullable=False),
        schema=S,
    )
    op.create_index("ix_integration_lineage_record", "lineage", ["snapshot_id", "record_type", "record_id"], schema=S)

    op.create_table(
        "quarantine",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("record_type", sa.String(16), nullable=False),
        sa.Column("record_id", sa.String(160), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=False),
        sa.Column("field_path", sa.String(128), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=S,
    )
    op.create_index("ix_integration_quarantine_status", "quarantine", ["status", "created_at"], schema=S)


def downgrade() -> None:
    for t in ("quarantine", "lineage", "transport_records", "weather_records", "disaster_events", "snapshots"):
        op.drop_table(t, schema=S)
