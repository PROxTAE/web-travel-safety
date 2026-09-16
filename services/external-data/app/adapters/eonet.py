"""NASA EONET v3 adapter — https://eonet.gsfc.nasa.gov/docs/v3

EONET is curated near-real-time metadata (not an official warning); authority = INTERGOVERNMENTAL,
``official=False``. Underlying source links are preserved in the description.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sta_common.http import ResilientClient
from sta_contracts.enums import DisasterEventType, ProviderKind, SourceAuthority
from sta_contracts.geo import BBox, Point, Polygon
from sta_contracts.models import DisasterEvent

from app.adapters.base import BaseAdapter, ProviderDescriptor
from app.domain.canonical import EONET_CATEGORY_TYPE, eonet_severity, provenance, quality


class _Geometry(BaseModel):
    model_config = ConfigDict(extra="allow")
    date: str
    type: str
    coordinates: Any
    magnitudeValue: float | None = None  # noqa: N815 - provider field name
    magnitudeUnit: str | None = None  # noqa: N815


class _Event(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    title: str
    description: str | None = None
    link: str | None = None
    closed: str | None = None
    categories: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    geometry: list[_Geometry] = Field(default_factory=list)


class _Response(BaseModel):
    model_config = ConfigDict(extra="allow")
    events: list[_Event]


class EonetAdapter(BaseAdapter):
    descriptor = ProviderDescriptor(
        name="eonet",
        kind=ProviderKind.DISASTER,
        version="v3",
        authority="INTERGOVERNMENTAL",
        license="NASA open data (public domain)",
        attribution="NASA Earth Observatory Natural Event Tracker (EONET)",
        docs_url="https://eonet.gsfc.nasa.gov/docs/v3",
        coverage_note="Curated natural events (storms, wildfires, volcanoes, floods); not an official warning source",
    )

    def __init__(self, base_url: str, *, service_name: str, ttl: int, **kw: Any) -> None:
        super().__init__(service_name=service_name, **kw)
        self.client = ResilientClient(
            service_name=service_name, dependency="eonet", base_url=base_url, read_timeout=10, total_timeout=12
        )
        self.ttl = ttl

    async def query(self, bbox: BBox, *, lookback_days: int, deadline: float | None = None) -> list[DisasterEvent]:
        # EONET bbox order is: min_lon,max_lat,max_lon,min_lat (upper-left, lower-right)
        params = {
            "status": "open",
            "days": str(max(1, lookback_days)),
            "bbox": f"{bbox.min_lon:.3f},{bbox.max_lat:.3f},{bbox.max_lon:.3f},{bbox.min_lat:.3f}",
            "limit": "100",
        }
        resp = await self._call(self.client, "GET", "/events", params=params, deadline=deadline)
        parsed = _Response.model_validate(self._json(resp, "eonet"))
        fetched_at = datetime.now(UTC)
        out: list[DisasterEvent] = []
        for ev in parsed.events:
            if not ev.geometry:
                continue
            latest = max(ev.geometry, key=lambda g: g.date)
            geometry: Point | Polygon
            if latest.type == "Point" and isinstance(latest.coordinates, list) and len(latest.coordinates) >= 2:
                geometry = Point(coordinates=[float(latest.coordinates[0]), float(latest.coordinates[1])])
            elif latest.type == "Polygon":
                geometry = Polygon(coordinates=latest.coordinates)
            else:
                continue
            cat_id = str((ev.categories[0] if ev.categories else {}).get("id", ""))
            etype = EONET_CATEGORY_TYPE.get(cat_id, DisasterEventType.OTHER)
            first = min(ev.geometry, key=lambda g: g.date)
            started = datetime.fromisoformat(first.date.replace("Z", "+00:00")).astimezone(UTC)
            observed = datetime.fromisoformat(latest.date.replace("Z", "+00:00")).astimezone(UTC)
            source_links = "; ".join(f"{s.get('id')}: {s.get('url')}" for s in ev.sources if s.get("url"))
            desc_parts = [p for p in (ev.description, f"Sources: {source_links}" if source_links else None) if p]
            raw = ev.model_dump(exclude_none=True)
            out.append(
                DisasterEvent(
                    event_id=f"eonet:{ev.id}",
                    event_type=etype,
                    title=ev.title[:300],
                    description=(" | ".join(desc_parts) or None),
                    severity=eonet_severity(cat_id, latest.magnitudeValue, latest.magnitudeUnit),
                    magnitude=latest.magnitudeValue,
                    geometry=geometry,
                    effective_at=started,
                    ends_at=None,
                    updated_at=observed,
                    official=False,
                    closure=False,
                    quality=quality(observed_at=observed, fetched_at=fetched_at, ttl_seconds=self.ttl, coverage=1.0),
                    source=provenance(
                        provider="eonet",
                        record_id=ev.id,
                        authority=SourceAuthority.INTERGOVERNMENTAL,
                        source_url=ev.link or f"https://eonet.gsfc.nasa.gov/api/v3/events/{ev.id}",
                        license_=self.descriptor.license,
                        attribution=self.descriptor.attribution,
                        observed_at=observed,
                        published_at=None,
                        fetched_at=fetched_at,
                        ttl_seconds=self.ttl,
                        raw=raw,
                    ),
                )
            )
        return out

    async def close(self) -> None:
        await self.client.aclose()
