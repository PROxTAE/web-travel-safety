"""openrouteservice adapter — Directions v2 (GeoJSON) and POIs.

Docs: https://openrouteservice.org/dev/#/api-docs/v2/directions ; limits: https://openrouteservice.org/restrictions/
- API key is server-side only (Authorization header).
- alternative routes only for requests under ORS_ALTERNATIVES_MAX_KM; avoid polygons under ORS_AVOID_MAX_KM.
- Unsupported travel modes raise OUTSIDE_COVERAGE — never silently mapped to "driving-car".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pyproj import Geod
from sta_common.http import ResilientClient
from sta_contracts.enums import ProviderKind, QualityFlag, RouteLabel, SourceAuthority, TravelMode
from sta_contracts.geo import LineString, Point, Polygon
from sta_contracts.models import RouteCandidate, RouteSegment

from app.adapters.base import BaseAdapter, ProviderDescriptor, ProviderError, ProviderErrorCode
from app.domain.canonical import deterministic_id, provenance, quality

PROFILE_BY_MODE: dict[TravelMode, str] = {
    TravelMode.CAR: "driving-car",
    TravelMode.WALK: "foot-walking",
    TravelMode.BICYCLE: "cycling-regular",
}
ALTERNATIVES_MAX_KM = 100.0  # provider restriction
AVOID_POLYGON_MAX_KM = 150.0  # provider restriction on route length with avoid polygons
_GEOD = Geod(ellps="WGS84")


class _Summary(BaseModel):
    model_config = ConfigDict(extra="allow")
    distance: float
    duration: float


class _Step(BaseModel):
    model_config = ConfigDict(extra="allow")
    distance: float
    duration: float
    instruction: str | None = None
    way_points: list[int] = Field(default_factory=list)


class _Segment(BaseModel):
    model_config = ConfigDict(extra="allow")
    distance: float
    duration: float
    steps: list[_Step] = Field(default_factory=list)


class _Props(BaseModel):
    model_config = ConfigDict(extra="allow")
    summary: _Summary
    segments: list[_Segment] = Field(default_factory=list)
    way_points: list[int] = Field(default_factory=list)


class _Feature(BaseModel):
    model_config = ConfigDict(extra="allow")
    properties: _Props
    geometry: dict[str, Any]


class _Response(BaseModel):
    model_config = ConfigDict(extra="allow")
    features: list[_Feature]
    metadata: dict[str, Any] | None = None


class OpenRouteServiceAdapter(BaseAdapter):
    descriptor = ProviderDescriptor(
        name="openrouteservice",
        kind=ProviderKind.ROUTE,
        version="v2",
        authority="LICENSED_PROVIDER",
        license="openrouteservice API terms; map data © OpenStreetMap contributors (ODbL)",
        attribution="Routing by openrouteservice.org | Map data © OpenStreetMap contributors",
        docs_url="https://openrouteservice.org/dev/#/api-docs/v2/directions",
        coverage_note="Road routing worldwide for car/walk/bicycle; alternatives < 100 km; avoid polygons < 150 km",
    )

    def __init__(
        self, base_url: str, api_key: str, *, service_name: str, ttl_route: int, ttl_places: int, **kw: Any
    ) -> None:
        super().__init__(service_name=service_name, **kw)
        self.descriptor.enabled = bool(api_key)
        if not api_key:
            self.descriptor.coverage_note = "UNAVAILABLE: ORS_API_KEY not configured"
        self.client = ResilientClient(
            service_name=service_name,
            dependency="openrouteservice",
            base_url=base_url,
            read_timeout=10,
            total_timeout=12,
            default_headers={"Authorization": api_key} if api_key else None,
        )
        self.ttl_route = ttl_route
        self.ttl_places = ttl_places
        self._category_cache: dict[str, int] | None = None

    # ------------------------------------------------------------------ routes
    async def directions(
        self,
        origin: Point,
        destination: Point,
        mode: TravelMode,
        *,
        departure_time: datetime,
        avoid_polygons: list[Polygon] | None = None,
        want_alternatives: bool = True,
        deadline: float | None = None,
    ) -> list[RouteCandidate]:
        if not self.descriptor.enabled:
            raise ProviderError(ProviderErrorCode.NOT_CONFIGURED, "openrouteservice: ORS_API_KEY not configured")
        profile = PROFILE_BY_MODE.get(mode)
        if profile is None:
            raise ProviderError(
                ProviderErrorCode.OUTSIDE_COVERAGE, f"openrouteservice does not route mode {mode.value}"
            )
        _, _, straight_m = _GEOD.inv(origin.lon, origin.lat, destination.lon, destination.lat)
        body: dict[str, Any] = {
            "coordinates": [[origin.lon, origin.lat], [destination.lon, destination.lat]],
            "instructions": True,
            "units": "m",
        }
        alternatives_requested = False
        if want_alternatives and straight_m / 1000 < ALTERNATIVES_MAX_KM and not avoid_polygons:
            body["alternative_routes"] = {"target_count": 3, "share_factor": 0.6, "weight_factor": 1.4}
            alternatives_requested = True
        if avoid_polygons:
            if straight_m / 1000 >= AVOID_POLYGON_MAX_KM:
                raise ProviderError(
                    ProviderErrorCode.OUTSIDE_COVERAGE,
                    f"openrouteservice avoid polygons are limited to routes under {AVOID_POLYGON_MAX_KM:.0f} km",
                )
            if len(avoid_polygons) == 1:
                body["options"] = {"avoid_polygons": avoid_polygons[0].model_dump()}
            else:
                body["options"] = {
                    "avoid_polygons": {"type": "MultiPolygon", "coordinates": [p.coordinates for p in avoid_polygons]}
                }
        resp = await self._call(self.client, "POST", f"/v2/directions/{profile}/geojson", json=body, deadline=deadline)
        parsed = _Response.model_validate(self._json(resp, "openrouteservice"))
        fetched_at = datetime.now(UTC)
        out: list[RouteCandidate] = []
        for i, feat in enumerate(parsed.features):
            coords = feat.geometry.get("coordinates") or []
            if feat.geometry.get("type") != "LineString" or len(coords) < 2:
                raise ProviderError(ProviderErrorCode.PROVIDER_SCHEMA_CHANGED, "route geometry is not a LineString")
            line = LineString(coordinates=[[float(c[0]), float(c[1])] for c in coords])
            segments = _segments(feat, line, mode, departure_time)
            raw = {
                "summary": feat.properties.summary.model_dump(),
                "n_points": len(coords),
                "profile": profile,
                "index": i,
            }
            src = provenance(
                provider="openrouteservice",
                record_id=f"{profile}:{i}:{fetched_at.timestamp():.0f}",
                authority=SourceAuthority.LICENSED_PROVIDER,
                source_url=self.descriptor.docs_url,
                license_=self.descriptor.license,
                attribution=self.descriptor.attribution,
                observed_at=fetched_at,
                published_at=None,
                fetched_at=fetched_at,
                ttl_seconds=self.ttl_route,
                raw=raw,
            )
            notes = [] if alternatives_requested or i > 0 else ["alternatives not requested (distance/avoid limits)"]
            out.append(
                RouteCandidate(
                    route_id=deterministic_id("ors", profile, str(i), src.content_hash),
                    provider_route_id=src.provider_record_id,
                    label=RouteLabel.ORIGINAL if i == 0 else RouteLabel.ALTERNATIVE,
                    mode=mode,
                    geometry=line,
                    segments=segments,
                    distance_m=feat.properties.summary.distance,
                    duration_seconds=feat.properties.summary.duration,
                    transfers=0,
                    quality=quality(
                        observed_at=fetched_at,
                        fetched_at=fetched_at,
                        ttl_seconds=self.ttl_route,
                        coverage=1.0,
                        notes=notes,
                    ),
                    sources=[src],
                )
            )
        return out

    # ------------------------------------------------------------------ POIs
    async def category_ids(self, names: list[str], *, deadline: float | None = None) -> list[int]:
        """Resolve ORS POI category names to ids via the provider's own list endpoint (no hard-coded ids)."""
        if self._category_cache is None:
            resp = await self._call(self.client, "POST", "/pois", json={"request": "list"}, deadline=deadline)
            data = self._json(resp, "openrouteservice")
            cache: dict[str, int] = {}
            for _group, spec in (data or {}).items():
                for cname, cid in (spec.get("children") or {}).items():
                    if isinstance(cid, int):
                        cache[cname] = cid
            self._category_cache = cache
        ids = [self._category_cache[n] for n in names if n in self._category_cache]
        if not ids:
            raise ProviderError(
                ProviderErrorCode.OUTSIDE_COVERAGE, f"openrouteservice has no POI categories for {names}"
            )
        return ids

    async def nearby(
        self, point: Point, *, radius_m: int, category_names: list[str], limit: int, deadline: float | None = None
    ) -> dict[str, Any]:
        if not self.descriptor.enabled:
            raise ProviderError(ProviderErrorCode.NOT_CONFIGURED, "openrouteservice: ORS_API_KEY not configured")
        ids = await self.category_ids(category_names, deadline=deadline)
        body = {
            "request": "pois",
            "geometry": {
                "geojson": {"type": "Point", "coordinates": [point.lon, point.lat]},
                "buffer": min(radius_m, 2000),
            },
            "filters": {"category_ids": ids},
            "limit": min(limit, 200),
            "sortby": "distance",
        }
        resp = await self._call(self.client, "POST", "/pois", json=body, deadline=deadline)
        data = self._json(resp, "openrouteservice")
        fetched_at = datetime.now(UTC)
        features = []
        for f in (data.get("features") or [])[:limit]:
            props = f.get("properties") or {}
            tags = props.get("osm_tags") or {}
            geom = f.get("geometry") or {}
            if geom.get("type") != "Point":
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": geom,
                    "properties": {
                        "provider_poi_id": str(props.get("osm_id")),
                        "name": tags.get("name"),
                        "category": ",".join(str(k) for k in (props.get("category_ids") or {}).keys()),
                        # Only pass through a phone if the provider (OSM) has one; never invent it.
                        "phone": tags.get("phone") or None,
                        "website": tags.get("website") or None,
                        "distance_m": props.get("distance"),
                        "source": "openrouteservice/OpenStreetMap",
                        "attribution": self.descriptor.attribution,
                        "fetched_at": fetched_at.isoformat(),
                        "expires_at": (fetched_at + timedelta(seconds=self.ttl_places)).isoformat(),
                    },
                }
            )
        return {
            "type": "FeatureCollection",
            "features": features,
            "attribution": self.descriptor.attribution,
            "fetched_at": fetched_at.isoformat(),
        }

    async def close(self) -> None:
        await self.client.aclose()


def _segments(feat: _Feature, line: LineString, mode: TravelMode, departure_time: datetime) -> list[RouteSegment]:
    segs: list[RouteSegment] = []
    t = departure_time
    coords = line.coordinates
    for i, s in enumerate(feat.properties.segments):
        wp = feat.properties.way_points
        start = wp[i] if i < len(wp) else 0
        end = wp[i + 1] if i + 1 < len(wp) else len(coords) - 1
        sub = coords[start : end + 1] if end > start else coords[max(0, start - 1) : start + 1]
        if len(sub) < 2:
            sub = coords[:2]
        eta_end = t + timedelta(seconds=s.duration)
        segs.append(
            RouteSegment(
                index=i,
                mode=mode,
                geometry=LineString(coordinates=sub),
                distance_m=s.distance,
                duration_seconds=s.duration,
                eta_start=t,
                eta_end=eta_end,
                instruction=(s.steps[0].instruction if s.steps else None),
            )
        )
        t = eta_end
    if not segs:
        segs.append(
            RouteSegment(
                index=0,
                mode=mode,
                geometry=line,
                distance_m=feat.properties.summary.distance,
                duration_seconds=feat.properties.summary.duration,
                eta_start=departure_time,
                eta_end=departure_time + timedelta(seconds=feat.properties.summary.duration),
            )
        )
    return segs


__all__ = ["OpenRouteServiceAdapter", "PROFILE_BY_MODE", "QualityFlag"]
