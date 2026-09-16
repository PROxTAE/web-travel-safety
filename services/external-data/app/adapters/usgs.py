"""USGS earthquake catalog adapter — https://earthquake.usgs.gov/fdsnws/event/1/"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict
from sta_common.http import ResilientClient
from sta_contracts.enums import DisasterEventType, ProviderKind, SourceAuthority
from sta_contracts.geo import BBox, Point
from sta_contracts.models import DisasterEvent

from app.adapters.base import BaseAdapter, ProviderDescriptor, ProviderError, ProviderErrorCode
from app.domain.canonical import provenance, quality, usgs_severity


class _Props(BaseModel):
    model_config = ConfigDict(extra="allow")
    mag: float | None = None
    place: str | None = None
    time: int
    updated: int | None = None
    url: str | None = None
    alert: str | None = None
    tsunami: int | None = None
    title: str | None = None
    status: str | None = None


class _Feature(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    properties: _Props
    geometry: dict[str, Any]


class _Collection(BaseModel):
    model_config = ConfigDict(extra="allow")
    features: list[_Feature]


class UsgsAdapter(BaseAdapter):
    descriptor = ProviderDescriptor(
        name="usgs",
        kind=ProviderKind.DISASTER,
        version="fdsnws-1",
        authority="OFFICIAL",
        license="Public domain (U.S. Government work)",
        attribution="U.S. Geological Survey Earthquake Hazards Program",
        docs_url="https://earthquake.usgs.gov/fdsnws/event/1/",
        coverage_note="Global earthquakes; magnitudes and depths from USGS NEIC",
    )

    def __init__(self, query_url: str, *, service_name: str, ttl: int, min_magnitude: float, **kw: Any) -> None:
        super().__init__(service_name=service_name, **kw)
        parsed = urlsplit(query_url)
        self._path = parsed.path or "/fdsnws/event/1/query"
        self.client = ResilientClient(
            service_name=service_name,
            dependency="usgs",
            base_url=f"{parsed.scheme}://{parsed.netloc}",
            read_timeout=8,
            total_timeout=10,
        )
        self.ttl = ttl
        self.min_magnitude = min_magnitude

    async def query(self, bbox: BBox, *, lookback_days: int, deadline: float | None = None) -> list[DisasterEvent]:
        start = datetime.now(UTC) - timedelta(days=lookback_days)
        params = {
            "format": "geojson",
            "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "minlatitude": f"{bbox.min_lat:.3f}",
            "maxlatitude": f"{bbox.max_lat:.3f}",
            "minlongitude": f"{bbox.min_lon:.3f}",
            "maxlongitude": f"{bbox.max_lon:.3f}",
            "minmagnitude": str(self.min_magnitude),
            "orderby": "time",
            "limit": "200",
        }
        resp = await self._call(self.client, "GET", self._path, params=params, deadline=deadline)
        parsed = _Collection.model_validate(self._json(resp, "usgs"))
        fetched_at = datetime.now(UTC)
        out: list[DisasterEvent] = []
        for f in parsed.features:
            coords = f.geometry.get("coordinates") or []
            if f.geometry.get("type") != "Point" or len(coords) < 2:
                raise ProviderError(ProviderErrorCode.PROVIDER_SCHEMA_CHANGED, "USGS feature geometry is not a Point")
            occurred = datetime.fromtimestamp(f.properties.time / 1000, tz=UTC)
            updated = datetime.fromtimestamp(f.properties.updated / 1000, tz=UTC) if f.properties.updated else None
            raw = {"id": f.id, **f.properties.model_dump(exclude_none=True), "coordinates": coords}
            out.append(
                DisasterEvent(
                    event_id=f"usgs:{f.id}",
                    event_type=DisasterEventType.EARTHQUAKE,
                    title=(f.properties.title or f"M {f.properties.mag} - {f.properties.place}")[:300],
                    description=f"Depth {coords[2]:.1f} km" if len(coords) > 2 and coords[2] is not None else None,
                    severity=usgs_severity(f.properties.mag),
                    magnitude=f.properties.mag,
                    geometry=Point(coordinates=[float(coords[0]), float(coords[1])]),
                    effective_at=occurred,
                    ends_at=None,
                    updated_at=updated,
                    instruction=None,
                    official=True,
                    closure=False,
                    quality=quality(observed_at=occurred, fetched_at=fetched_at, ttl_seconds=self.ttl, coverage=1.0),
                    source=provenance(
                        provider="usgs",
                        record_id=f.id,
                        authority=SourceAuthority.OFFICIAL,
                        source_url=f.properties.url or f"https://earthquake.usgs.gov/earthquakes/eventpage/{f.id}",
                        license_=self.descriptor.license,
                        attribution=self.descriptor.attribution,
                        observed_at=occurred,
                        published_at=updated,
                        fetched_at=fetched_at,
                        ttl_seconds=self.ttl,
                        raw=raw,
                    ),
                )
            )
        return out

    async def close(self) -> None:
        await self.client.aclose()
