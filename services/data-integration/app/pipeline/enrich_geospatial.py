"""Stage 5 — route corridor + spatial/temporal alignment.

- Storage CRS is EPSG:4326. Buffers/distances are computed in a local azimuthal-equidistant projection
  centred on the route (metres), then transformed back. Handles the antimeridian by splitting on the
  projected side (AEQD has no dateline discontinuity near the route centre).
- Events intersect the corridor geometrically AND overlap the travel window temporally.
- Events are buffered by severity (versioned table) so a point centroid (USGS/GDACS) gets an area of effect.
- Weather points are matched to the nearest route sample within distance/time tolerance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import h3
from pyproj import CRS, Geod, Transformer
from shapely.geometry import LineString, Point, box, mapping, shape
from shapely.geometry import MultiPolygon as ShapelyMultiPolygon
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform
from shapely.validation import make_valid
from sta_contracts.enums import SEVERITY_RANK, DisasterEventType, Severity, TravelMode
from sta_contracts.geo import MultiPolygon, Polygon
from sta_contracts.models import DisasterEvent, RouteCandidate, RouteExposure, TravelWindow, WeatherForecastPoint

GEOD = Geod(ellps="WGS84")
EVENT_BUFFER_VERSION = "1.0.0"
# area-of-effect radius (km) by event type and severity when the provider only gives a centroid
EVENT_BUFFER_KM: dict[DisasterEventType, dict[Severity, float]] = {
    DisasterEventType.EARTHQUAKE: {
        Severity.INFO: 10,
        Severity.MINOR: 30,
        Severity.MODERATE: 80,
        Severity.SEVERE: 150,
        Severity.EXTREME: 300,
        Severity.UNKNOWN: 30,
    },
    DisasterEventType.CYCLONE: {
        Severity.INFO: 50,
        Severity.MINOR: 100,
        Severity.MODERATE: 200,
        Severity.SEVERE: 300,
        Severity.EXTREME: 400,
        Severity.UNKNOWN: 150,
    },
    DisasterEventType.STORM: {
        Severity.INFO: 30,
        Severity.MINOR: 60,
        Severity.MODERATE: 120,
        Severity.SEVERE: 200,
        Severity.EXTREME: 300,
        Severity.UNKNOWN: 100,
    },
    DisasterEventType.FLOOD: {
        Severity.INFO: 20,
        Severity.MINOR: 40,
        Severity.MODERATE: 80,
        Severity.SEVERE: 120,
        Severity.EXTREME: 200,
        Severity.UNKNOWN: 50,
    },
    DisasterEventType.WILDFIRE: {
        Severity.INFO: 10,
        Severity.MINOR: 20,
        Severity.MODERATE: 40,
        Severity.SEVERE: 80,
        Severity.EXTREME: 120,
        Severity.UNKNOWN: 30,
    },
    DisasterEventType.VOLCANO: {
        Severity.INFO: 20,
        Severity.MINOR: 40,
        Severity.MODERATE: 80,
        Severity.SEVERE: 150,
        Severity.EXTREME: 300,
        Severity.UNKNOWN: 50,
    },
    DisasterEventType.LANDSLIDE: {
        Severity.INFO: 5,
        Severity.MINOR: 10,
        Severity.MODERATE: 20,
        Severity.SEVERE: 40,
        Severity.EXTREME: 60,
        Severity.UNKNOWN: 15,
    },
    DisasterEventType.EXTREME_TEMPERATURE: {
        Severity.INFO: 50,
        Severity.MINOR: 100,
        Severity.MODERATE: 200,
        Severity.SEVERE: 300,
        Severity.EXTREME: 400,
        Severity.UNKNOWN: 100,
    },
    DisasterEventType.HEALTH: {
        Severity.INFO: 20,
        Severity.MINOR: 50,
        Severity.MODERATE: 100,
        Severity.SEVERE: 200,
        Severity.EXTREME: 300,
        Severity.UNKNOWN: 50,
    },
    DisasterEventType.TRANSPORT_CLOSURE: {
        Severity.INFO: 2,
        Severity.MINOR: 5,
        Severity.MODERATE: 10,
        Severity.SEVERE: 20,
        Severity.EXTREME: 30,
        Severity.UNKNOWN: 5,
    },
    DisasterEventType.OTHER: {
        Severity.INFO: 10,
        Severity.MINOR: 20,
        Severity.MODERATE: 40,
        Severity.SEVERE: 80,
        Severity.EXTREME: 120,
        Severity.UNKNOWN: 30,
    },
}
# how long an event without ``ends_at`` is considered active after effective_at (hours)
DEFAULT_ACTIVE_HOURS: dict[DisasterEventType, float] = {
    DisasterEventType.EARTHQUAKE: 72,
    DisasterEventType.CYCLONE: 120,
    DisasterEventType.STORM: 48,
    DisasterEventType.FLOOD: 240,
    DisasterEventType.WILDFIRE: 240,
    DisasterEventType.VOLCANO: 720,
    DisasterEventType.LANDSLIDE: 168,
    DisasterEventType.EXTREME_TEMPERATURE: 120,
    DisasterEventType.HEALTH: 720,
    DisasterEventType.TRANSPORT_CLOSURE: 24,
    DisasterEventType.OTHER: 72,
}


@dataclass(slots=True)
class LocalProjection:
    to_local: Transformer
    to_wgs: Transformer

    @classmethod
    def around(cls, lon: float, lat: float) -> LocalProjection:
        local = CRS.from_proj4(f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs")
        wgs = CRS.from_epsg(4326)
        return cls(
            to_local=Transformer.from_crs(wgs, local, always_xy=True),
            to_wgs=Transformer.from_crs(local, wgs, always_xy=True),
        )

    def local(self, geom: BaseGeometry) -> BaseGeometry:
        return transform(self.to_local.transform, geom)

    def wgs(self, geom: BaseGeometry) -> BaseGeometry:
        return transform(self.to_wgs.transform, geom)


@dataclass(slots=True)
class Corridor:
    polygon_wgs: Polygon | MultiPolygon
    buffer_m: float
    line_local: LineString
    proj: LocalProjection
    length_m: float
    h3_cells: list[str] = field(default_factory=list)


def buffer_for_mode(modes: list[TravelMode], *, ground_m: float, flight_m: float) -> float:
    return flight_m if TravelMode.FLIGHT in modes else ground_m


def densify(coords: list[list[float]], max_spacing_m: float) -> list[list[float]]:
    out: list[list[float]] = [coords[0]]
    for (x1, y1, *_), (x2, y2, *_) in zip(coords, coords[1:], strict=False):
        _, _, d = GEOD.inv(x1, y1, x2, y2)
        n = int(d // max_spacing_m)
        if n > 0:
            out.extend([[float(x), float(y)] for x, y in GEOD.npts(x1, y1, x2, y2, n)])
        out.append([x2, y2])
    return out


def geodesic_midpoint(coords: list[list[float]]) -> tuple[float, float]:
    cum = [0.0]
    for (x1, y1, *_), (x2, y2, *_) in zip(coords, coords[1:], strict=False):
        _, _, d = GEOD.inv(x1, y1, x2, y2)
        cum.append(cum[-1] + d)
    half = cum[-1] / 2
    for i in range(1, len(cum)):
        if cum[i] >= half:
            seg = cum[i] - cum[i - 1]
            frac = 0.0 if seg == 0 else (half - cum[i - 1]) / seg
            x1, y1 = coords[i - 1][:2]
            x2, y2 = coords[i][:2]
            az, _, d = GEOD.inv(x1, y1, x2, y2)
            lon, lat, _ = GEOD.fwd(x1, y1, az, d * frac)
            return float(lon), float(lat)
    return float(coords[0][0]), float(coords[0][1])


def build_corridor(route: RouteCandidate, *, buffer_m: float, densify_m: float) -> Corridor:
    coords = densify(route.geometry.coordinates, densify_m)
    line = LineString([(c[0], c[1]) for c in coords])
    # centre the local projection on the geodesic midpoint (a planar centroid breaks at the antimeridian)
    mid_lon, mid_lat = geodesic_midpoint(coords)
    proj = LocalProjection.around(mid_lon, mid_lat)
    line_local = proj.local(line)
    poly_local = line_local.buffer(buffer_m, quad_segs=8)
    poly_wgs = proj.wgs(poly_local)
    if poly_wgs.geom_type == "MultiPolygon":
        poly_wgs = max(poly_wgs.geoms, key=lambda g: g.area)
    length_m = float(line_local.length)
    cells: set[str] = set()
    for x, y in coords:
        cells.add(h3.latlng_to_cell(y, x, 3))
    return Corridor(
        polygon_wgs=split_antimeridian(poly_wgs),
        buffer_m=buffer_m,
        line_local=line_local,
        proj=proj,
        length_m=length_m,
        h3_cells=sorted(cells),
    )


def active_window(e: DisasterEvent) -> tuple[datetime | None, datetime | None]:
    start = e.effective_at
    end = e.ends_at
    if start is not None and end is None:
        end = start + timedelta(hours=DEFAULT_ACTIVE_HOURS[e.event_type])
    return start, end


def overlaps(window: TravelWindow, e: DisasterEvent) -> bool:
    start, end = active_window(e)
    if start is None and end is None:
        return True  # unknown timing: conservatively treat as active (flagged elsewhere)
    dep, arr = window.departure_at, window.arrival_at
    if start is not None and start > arr:
        return False
    return not (end is not None and end < dep)


@dataclass(slots=True)
class EventHit:
    event: DisasterEvent
    distance_to_route_m: float
    inside_corridor: bool
    time_overlap: bool


def intersect_events(corridor: Corridor, events: list[DisasterEvent], window: TravelWindow) -> list[EventHit]:
    hits: list[EventHit] = []
    for e in events:
        g = shape(e.geometry.model_dump())
        g_local = corridor.proj.local(g)
        if g_local.geom_type == "Point":
            radius_km = EVENT_BUFFER_KM.get(e.event_type, EVENT_BUFFER_KM[DisasterEventType.OTHER])[e.severity]
            area = g_local.buffer(radius_km * 1000)
        else:
            area = g_local
        dist = float(g_local.distance(corridor.line_local))
        inside = bool(area.intersects(corridor.line_local.buffer(corridor.buffer_m)))
        hits.append(
            EventHit(event=e, distance_to_route_m=dist, inside_corridor=inside, time_overlap=overlaps(window, e))
        )
    return hits


@dataclass(slots=True)
class WeatherMatch:
    point: WeatherForecastPoint
    sample_index: int
    distance_m: float
    time_delta_s: float
    minutes_covered: float


def match_weather(
    corridor: Corridor,
    route: RouteCandidate,
    weather: list[WeatherForecastPoint],
    *,
    departure_at: datetime,
    max_distance_m: float,
    tolerance_s: int,
    expected_spacing_m: float = 50_000,
    expected_samples_max: int = 12,
) -> tuple[list[WeatherMatch], float]:
    """Match forecast points to the route by distance + ETA tolerance.

    Coverage = fraction of *expected* sample positions (derived from route length, not from the input)
    that have a matched point nearby, so sparse data can never claim full coverage.
    """
    expected = max(2, min(expected_samples_max, int(corridor.length_m // expected_spacing_m) + 1))
    if not weather:
        return [], 0.0
    matches: list[WeatherMatch] = []
    total_s = route.duration_seconds or 1.0
    fractions: list[float] = []
    for w in weather:
        p_local = corridor.proj.local(Point(w.location.coordinates[0], w.location.coordinates[1]))
        dist = float(p_local.distance(corridor.line_local))
        if dist > max_distance_m:
            continue
        frac = float(corridor.line_local.project(p_local, normalized=True))
        eta = departure_at + timedelta(seconds=total_s * frac)
        dt = abs((w.valid_at - eta).total_seconds())
        if dt > tolerance_s:
            continue
        idx = w.route_sample_index if w.route_sample_index is not None else int(round(frac * (expected - 1)))
        fractions.append(frac)
        matches.append(
            WeatherMatch(
                point=w, sample_index=idx, distance_m=dist, time_delta_s=dt, minutes_covered=total_s / 60 / expected
            )
        )
    half = 0.5 / (expected - 1)
    covered = sum(1 for i in range(expected) if any(abs(f - i / (expected - 1)) <= half for f in fractions))
    return matches, round(min(1.0, covered / expected), 3)


def exposure_for_route(
    corridor: Corridor, route: RouteCandidate, hits: list[EventHit], wx: list[WeatherMatch]
) -> RouteExposure:
    active = [h for h in hits if h.inside_corridor and h.time_overlap]
    severe_minutes = sum(
        m.minutes_covered for m in wx if SEVERITY_RANK[m.point.severity] >= SEVERITY_RANK[Severity.SEVERE]
    )
    max_sev = max((h.event.severity for h in active), key=lambda s: SEVERITY_RANK[s], default=Severity.UNKNOWN)
    max_wx = max((m.point.severity for m in wx), key=lambda s: SEVERITY_RANK[s], default=Severity.UNKNOWN)
    if SEVERITY_RANK[max_wx] > SEVERITY_RANK[max_sev]:
        max_sev = max_wx
    closed = any(h.event.closure for h in active)
    min_dist = min((h.distance_to_route_m for h in hits), default=None)
    # exposure score v1.0.0: weighted mix, capped to [0,1]; not a risk decision
    score = 0.0
    for h in active:
        score += {
            Severity.INFO: 0.05,
            Severity.MINOR: 0.1,
            Severity.MODERATE: 0.25,
            Severity.SEVERE: 0.5,
            Severity.EXTREME: 0.8,
            Severity.UNKNOWN: 0.15,
        }[h.event.severity]
    if route.duration_seconds:
        score += min(0.6, severe_minutes / (route.duration_seconds / 60)) * 0.6
    if closed:
        score = 1.0
    return RouteExposure(
        score=round(min(1.0, score), 3),
        hazard_event_ids=[h.event.event_id for h in active],
        weather_window_ids=[
            m.point.id for m in wx if SEVERITY_RANK[m.point.severity] >= SEVERITY_RANK[Severity.MODERATE]
        ],
        closed=closed,
        severe_weather_minutes=round(severe_minutes, 1),
        max_hazard_severity=max_sev,
        min_hazard_distance_km=round(min_dist / 1000, 1) if min_dist is not None else None,
    )


def segments_hit_by_closures(corridor: Corridor, route: RouteCandidate, hits: list[EventHit]) -> int:
    closures = [h for h in hits if h.inside_corridor and h.time_overlap and h.event.closure]
    if not closures or not route.segments:
        return 1 if closures else 0
    n = 0
    for seg in route.segments:
        seg_local = corridor.proj.local(LineString([(c[0], c[1]) for c in seg.geometry.coordinates]))
        for h in closures:
            g_local = corridor.proj.local(shape(h.event.geometry.model_dump()))
            if g_local.geom_type == "Point":
                g_local = g_local.buffer(EVENT_BUFFER_KM[h.event.event_type][h.event.severity] * 1000)
            if g_local.intersects(seg_local):
                n += 1
                break
    return n


def split_antimeridian(poly: BaseGeometry) -> Polygon | MultiPolygon:
    """Return a valid GeoJSON polygon; corridors crossing ±180° are split into a MultiPolygon."""
    xs = [x for x, _ in poly.exterior.coords] if poly.geom_type == "Polygon" else []
    crosses = bool(xs) and (max(xs) - min(xs) > 180)
    if not crosses:
        fixed = poly if poly.is_valid else make_valid(poly)
        if fixed.geom_type == "MultiPolygon":
            return MultiPolygon.model_validate(mapping(fixed))
        if fixed.geom_type != "Polygon":
            fixed = max((g for g in getattr(fixed, "geoms", [fixed]) if g.geom_type == "Polygon"), key=lambda g: g.area)
        return polygon_from_shapely(fixed)
    shifted = transform(lambda x, y, z=None: (x + 360 if x < 0 else x, y), poly)
    if not shifted.is_valid:
        shifted = make_valid(shifted)
    west = shifted.intersection(box(0, -90, 180, 90))
    east = transform(lambda x, y, z=None: (x - 360, y), shifted.intersection(box(180, -90, 360, 90)))
    parts: list[ShapelyPolygon] = []
    for g in (west, east):
        parts.extend(
            p for p in (g.geoms if hasattr(g, "geoms") else [g]) if p.geom_type == "Polygon" and not p.is_empty
        )
    return MultiPolygon.model_validate(mapping(ShapelyMultiPolygon(parts)))


def polygon_from_shapely(poly: ShapelyPolygon) -> Polygon:
    geo = mapping(poly)
    return Polygon(coordinates=[[[float(x), float(y)] for x, y in ring] for ring in geo["coordinates"]])
