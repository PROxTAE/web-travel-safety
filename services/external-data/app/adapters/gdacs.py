"""GDACS event list adapter — https://www.gdacs.org/gdacsapi/swagger/index.html

Endpoint: GET /api/events/geteventlist/SEARCH?fromDate=&toDate=&alertlevel=Green;Orange;Red&eventlist=EQ;TC;FL;VO;WF;DR
Returns a GeoJSON FeatureCollection with Point centroids (polygon geometry is a separate URL).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict
from sta_common.http import ResilientClient
from sta_contracts.enums import DisasterEventType, ProviderKind, Severity, SourceAuthority
from sta_contracts.geo import BBox, Point
from sta_contracts.models import DisasterEvent

from app.adapters.base import BaseAdapter, ProviderDescriptor, ProviderError, ProviderErrorCode
from app.domain.canonical import GDACS_ALERT_SEVERITY, GDACS_EVENT_TYPE, provenance, quality

ATTRIBUTION = "Global Disaster Awareness and Coordination System, GDACS"


class _Props(BaseModel):
    model_config = ConfigDict(extra="allow")
    eventtype: str
    eventid: int
    episodeid: int | None = None
    name: str | None = None
    description: str | None = None
    alertlevel: str | None = None
    episodealertlevel: str | None = None
    iscurrent: str | bool | None = None
    country: str | None = None
    fromdate: str | None = None
    todate: str | None = None
    datemodified: str | None = None
    iso3: str | None = None
    url: dict[str, Any] | None = None
    affectedcountries: list[dict[str, Any]] | None = None
    severitydata: dict[str, Any] | None = None


class _Feature(BaseModel):
    model_config = ConfigDict(extra="allow")
    properties: _Props
    geometry: dict[str, Any]
    bbox: list[float] | None = None


class _Collection(BaseModel):
    model_config = ConfigDict(extra="allow")
    features: list[_Feature]


def _parse_gdacs_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


class GdacsAdapter(BaseAdapter):
    descriptor = ProviderDescriptor(
        name="gdacs",
        kind=ProviderKind.DISASTER,
        version="api-v1",
        authority="INTERGOVERNMENTAL",
        license="GDACS terms of use (attribution required)",
        attribution=ATTRIBUTION,
        docs_url="https://www.gdacs.org/gdacsapi/swagger/index.html",
        coverage_note="Earthquakes, cyclones, floods, volcanoes, wildfires, droughts (Green/Orange/Red alert levels)",
    )

    def __init__(self, base_url: str, *, service_name: str, ttl: int, **kw: Any) -> None:
        super().__init__(service_name=service_name, **kw)
        self.client = ResilientClient(
            service_name=service_name, dependency="gdacs", base_url=base_url, read_timeout=10, total_timeout=12
        )
        self.ttl = ttl

    async def query(self, bbox: BBox, *, lookback_days: int, deadline: float | None = None) -> list[DisasterEvent]:
        now = datetime.now(UTC)
        params = {
            "fromDate": (now - timedelta(days=lookback_days)).date().isoformat(),
            "toDate": (now + timedelta(days=1)).date().isoformat(),
            "alertlevel": "Green;Orange;Red",
            "eventlist": "EQ;TC;FL;VO;WF;DR",
        }
        resp = await self._call(self.client, "GET", "/api/events/geteventlist/SEARCH", params=params, deadline=deadline)
        parsed = _Collection.model_validate(self._json(resp, "gdacs"))
        fetched_at = datetime.now(UTC)
        out: list[DisasterEvent] = []
        for f in parsed.features:
            if f.geometry.get("type") != "Point":
                continue
            coords = f.geometry.get("coordinates") or []
            if len(coords) < 2:
                raise ProviderError(ProviderErrorCode.PROVIDER_SCHEMA_CHANGED, "GDACS point without coordinates")
            lon, lat = float(coords[0]), float(coords[1])
            # GDACS has no bbox filter; we filter client-side on centroid (คน 5 does precise corridor intersection).
            if not (bbox.min_lon <= lon <= bbox.max_lon and bbox.min_lat <= lat <= bbox.max_lat):
                continue
            p = f.properties
            level = (p.episodealertlevel or p.alertlevel or "").lower()
            severity = GDACS_ALERT_SEVERITY.get(level, Severity.UNKNOWN)
            etype = GDACS_EVENT_TYPE.get(p.eventtype.upper(), DisasterEventType.OTHER)
            magnitude = None
            if p.severitydata and isinstance(p.severitydata.get("severity"), int | float):
                magnitude = float(p.severitydata["severity"])
            started = _parse_gdacs_time(p.fromdate)
            ended = _parse_gdacs_time(p.todate)
            modified = _parse_gdacs_time(p.datemodified)
            is_current = str(p.iscurrent).lower() == "true"
            report_url = (p.url or {}).get(
                "report"
            ) or f"https://www.gdacs.org/report.aspx?eventid={p.eventid}&eventtype={p.eventtype}"
            countries = [
                c.get("iso2", "").upper()
                for c in (p.affectedcountries or [])
                if isinstance(c, dict) and len(c.get("iso2", "")) == 2
            ]
            raw = p.model_dump(exclude_none=True) | {"coordinates": coords}
            out.append(
                DisasterEvent(
                    event_id=f"gdacs:{p.eventtype}:{p.eventid}:{p.episodeid or 0}",
                    event_type=etype,
                    title=(p.name or p.description or f"GDACS {p.eventtype} {p.eventid}")[:300],
                    description=((p.severitydata or {}).get("severitytext") or p.description or None),
                    severity=severity,
                    magnitude=magnitude,
                    geometry=Point(coordinates=[lon, lat]),
                    effective_at=started,
                    ends_at=ended if not is_current else None,
                    updated_at=modified,
                    instruction=None,
                    official=level in ("orange", "red"),
                    closure=False,
                    country_codes=countries,
                    quality=quality(
                        observed_at=modified or started, fetched_at=fetched_at, ttl_seconds=self.ttl, coverage=1.0
                    ),
                    source=provenance(
                        provider="gdacs",
                        record_id=f"{p.eventtype}:{p.eventid}:{p.episodeid or 0}",
                        authority=SourceAuthority.INTERGOVERNMENTAL,
                        source_url=report_url,
                        license_=self.descriptor.license,
                        attribution=ATTRIBUTION,
                        observed_at=started,
                        published_at=modified,
                        fetched_at=fetched_at,
                        ttl_seconds=self.ttl,
                        raw=raw,
                    ),
                )
            )
        return out

    async def close(self) -> None:
        await self.client.aclose()
