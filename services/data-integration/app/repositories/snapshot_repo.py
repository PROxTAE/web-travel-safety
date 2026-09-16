"""Idempotent snapshot persistence on PostGIS (unique on request_id + input_content_hash + schema_version)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from geoalchemy2.shape import from_shape
from shapely.geometry import shape
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sta_contracts.models import IntegratedTravelContext

from app.pipeline.build_snapshot import BuildResult
from app.repositories.models import (
    DisasterEventRow,
    LineageRowModel,
    QuarantineRow,
    Snapshot,
    TransportRecordRow,
    WeatherRecordRow,
)


class SnapshotRepository:
    def __init__(self, factory: async_sessionmaker[AsyncSession] | None) -> None:
        self.factory = factory
        self._memory: dict[UUID, IntegratedTravelContext] = {}
        self._by_input: dict[tuple[str, str, str], UUID] = {}

    async def find_existing(
        self, request_id: UUID, input_hash: str, schema_version: str
    ) -> IntegratedTravelContext | None:
        if self.factory is None:
            sid = self._by_input.get((str(request_id), input_hash, schema_version))
            return self._memory.get(sid) if sid else None
        async with self.factory() as s:
            row = (
                await s.execute(
                    select(Snapshot).where(
                        Snapshot.request_id == request_id,
                        Snapshot.input_content_hash == input_hash,
                        Snapshot.schema_version == schema_version,
                    )
                )
            ).scalar_one_or_none()
            return IntegratedTravelContext.model_validate(row.snapshot_json) if row else None

    async def get(self, snapshot_id: UUID) -> IntegratedTravelContext | None:
        if self.factory is None:
            return self._memory.get(snapshot_id)
        async with self.factory() as s:
            row = await s.get(Snapshot, snapshot_id)
            return IntegratedTravelContext.model_validate(row.snapshot_json) if row else None

    async def latest_for_trip(self, trip_id: UUID) -> IntegratedTravelContext | None:
        if self.factory is None:
            cands = [v for v in self._memory.values() if v.trip_id == trip_id]
            return max(cands, key=lambda v: v.created_at) if cands else None
        async with self.factory() as s:
            row = (
                await s.execute(
                    select(Snapshot).where(Snapshot.trip_id == trip_id).order_by(Snapshot.created_at.desc()).limit(1)
                )
            ).scalar_one_or_none()
            return IntegratedTravelContext.model_validate(row.snapshot_json) if row else None

    async def persist(self, result: BuildResult) -> IntegratedTravelContext:
        snap = result.snapshot
        if self.factory is None:
            key = (str(snap.request_id), result.input_content_hash, snap.schema_version)
            if key in self._by_input:
                return self._memory[self._by_input[key]]
            self._memory[snap.snapshot_id] = snap
            self._by_input[key] = snap.snapshot_id
            return snap
        payload = json.loads(snap.model_dump_json())
        corridor = (
            from_shape(shape(snap.route_corridor_geojson.model_dump()), srid=4326)
            if snap.route_corridor_geojson
            else None
        )
        async with self.factory() as s:
            try:
                s.add(
                    Snapshot(
                        id=snap.snapshot_id,
                        request_id=snap.request_id,
                        trip_id=snap.trip_id,
                        trip_revision=snap.trip_revision,
                        input_content_hash=result.input_content_hash,
                        content_hash=snap.content_hash,
                        schema_version=snap.schema_version,
                        feature_schema_version=snap.feature_schema_version,
                        transform_version=snap.transform_version,
                        gate=snap.quality_summary.gate.value,
                        quality_json=payload["quality_summary"],
                        features_json=payload["features"],
                        snapshot_json=payload,
                        corridor=corridor,
                        departure_at=snap.travel_window.departure_at,
                        arrival_at=snap.travel_window.arrival_at,
                        supersedes_id=snap.supersedes_snapshot_id,
                        created_at=snap.created_at,
                    )
                )
                await s.flush()
            except IntegrityError:
                await s.rollback()
                existing = await self.find_existing(snap.request_id, result.input_content_hash, snap.schema_version)
                assert existing is not None
                return existing
            for e in snap.disaster_events:
                stmt = (
                    insert(DisasterEventRow)
                    .values(
                        event_id=e.event_id,
                        event_type=e.event_type.value,
                        severity=e.severity.value,
                        official=e.official,
                        closure=e.closure,
                        title=e.title,
                        provider=e.source.provider,
                        source_id=e.source.source_id,
                        content_hash=e.source.content_hash,
                        geom=from_shape(shape(e.geometry.model_dump()), srid=4326),
                        effective_at=e.effective_at,
                        ends_at=e.ends_at,
                        fetched_at=e.source.fetched_at,
                        expires_at=e.source.expires_at,
                        record_json=json.loads(e.model_dump_json()),
                    )
                    .on_conflict_do_nothing(constraint="uq_integration_event_hash")
                )
                await s.execute(stmt)
            for w in snap.weather:
                stmt = (
                    insert(WeatherRecordRow)
                    .values(
                        id=w.id,
                        snapshot_id=snap.snapshot_id,
                        valid_at=w.valid_at,
                        severity=w.severity.value,
                        provider=w.source.provider,
                        content_hash=w.source.content_hash,
                        geom=from_shape(shape(w.location.model_dump()), srid=4326),
                        record_json=json.loads(w.model_dump_json()),
                    )
                    .on_conflict_do_nothing(constraint="uq_integration_weather_hash")
                )
                await s.execute(stmt)
            for t in snap.transport:
                s.add(
                    TransportRecordRow(
                        id=t.id,
                        snapshot_id=snap.snapshot_id,
                        mode=t.mode.value,
                        operator=t.operator,
                        service_number=t.service_number,
                        status=t.status.value,
                        provider=t.source.provider,
                        fetched_at=t.source.fetched_at,
                        record_json=json.loads(t.model_dump_json()),
                    )
                )
            for ln in result.lineage:
                s.add(
                    LineageRowModel(
                        snapshot_id=snap.snapshot_id,
                        record_type=ln.record_type,
                        record_id=ln.record_id,
                        field_path=ln.field_path,
                        source_id=ln.source_id,
                        transform=ln.transform,
                        transform_version=ln.transform_version,
                    )
                )
            for q in result.quarantine:
                s.add(
                    QuarantineRow(
                        request_id=snap.request_id,
                        record_type=q.record_type,
                        record_id=q.record_id,
                        provider=q.provider,
                        error_code=q.error_code,
                        field_path=q.field_path,
                        content_hash=q.content_hash,
                        created_at=datetime.now(UTC),
                    )
                )
            await s.commit()
        return snap

    async def events_in_bbox(
        self, min_lon: float, min_lat: float, max_lon: float, max_lat: float, at: datetime, limit: int = 500
    ) -> list[dict[str, object]]:
        """Stored canonical events intersecting a bbox and active at ``at`` (for the safety map)."""
        if self.factory is None:
            out: list[dict[str, object]] = []
            for snap in self._memory.values():
                for e in snap.disaster_events:
                    out.append(json.loads(e.model_dump_json()))
            return out[:limit]
        from geoalchemy2.functions import ST_Intersects, ST_MakeEnvelope

        async with self.factory() as s:
            stmt = (
                select(DisasterEventRow.record_json)
                .where(ST_Intersects(DisasterEventRow.geom, ST_MakeEnvelope(min_lon, min_lat, max_lon, max_lat, 4326)))
                .where((DisasterEventRow.effective_at.is_(None)) | (DisasterEventRow.effective_at <= at))
                .where((DisasterEventRow.ends_at.is_(None)) | (DisasterEventRow.ends_at >= at))
                .order_by(DisasterEventRow.fetched_at.desc())
                .limit(limit)
            )
            rows = (await s.execute(stmt)).scalars().all()
            return [dict(r) for r in rows]
