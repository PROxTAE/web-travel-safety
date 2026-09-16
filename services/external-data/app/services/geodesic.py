"""Geodesic (great-circle) route geometry used when no route provider covers the mode/region.

This is *computed geometry*, not provider data. Every candidate produced here carries
``QualityFlag.INFERRED`` + ``OUTSIDE_COVERAGE``, a reduced quality score and an explicit note,
so downstream modules and the UI show it as an approximation rather than a real route.
Speed assumptions are versioned config, not hidden constants.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pyproj import Geod
from sta_contracts.enums import DataStatus, QualityFlag, RouteLabel, SourceAuthority, TravelMode
from sta_contracts.geo import LineString, Point
from sta_contracts.models import DataQuality, RouteCandidate, RouteSegment

from app.domain.canonical import deterministic_id, provenance

GEOD = Geod(ellps="WGS84")
ASSUMPTIONS_VERSION = "1.0.0"
# mean door-to-door speed (km/h) and fixed overhead (minutes) per mode — documented assumptions.
MODE_SPEED_KMH: dict[TravelMode, tuple[float, int]] = {
    TravelMode.FLIGHT: (750.0, 120),
    TravelMode.TRAIN: (80.0, 20),
    TravelMode.BUS: (60.0, 20),
    TravelMode.CAR: (70.0, 0),
    TravelMode.WALK: (4.5, 0),
    TravelMode.BICYCLE: (15.0, 0),
    TravelMode.MULTIMODAL: (60.0, 30),
}


def geodesic_route(
    origin: Point,
    destination: Point,
    mode: TravelMode,
    *,
    departure_time: datetime,
    reason: str,
    ttl_seconds: int,
    max_spacing_km: float = 25.0,
) -> RouteCandidate:
    az12, _, dist_m = GEOD.inv(origin.lon, origin.lat, destination.lon, destination.lat)
    n = max(2, int(dist_m / (max_spacing_km * 1000)) + 1)
    inner = GEOD.npts(origin.lon, origin.lat, destination.lon, destination.lat, n - 1) if n > 2 else []
    coords = [[origin.lon, origin.lat], *[[float(x), float(y)] for x, y in inner], [destination.lon, destination.lat]]
    speed, overhead = MODE_SPEED_KMH[mode]
    duration_s = dist_m / 1000 / speed * 3600 + overhead * 60
    fetched_at = datetime.now(UTC)
    line = LineString(coordinates=coords)
    src = provenance(
        provider="geodesic",
        record_id=None,
        authority=SourceAuthority.UNKNOWN,
        source_url=None,
        license_="computed",
        attribution="Great-circle approximation computed by Smart Travel Assistant",
        observed_at=None,
        published_at=None,
        fetched_at=fetched_at,
        ttl_seconds=ttl_seconds,
        raw={
            "origin": origin.coordinates,
            "destination": destination.coordinates,
            "mode": mode.value,
            "v": ASSUMPTIONS_VERSION,
        },
    )
    note = (
        f"{reason}; geometry is a great-circle approximation and duration assumes {speed:.0f} km/h "
        f"+ {overhead} min overhead (assumptions v{ASSUMPTIONS_VERSION})"
    )
    return RouteCandidate(
        route_id=deterministic_id("geodesic", mode.value, src.content_hash),
        provider_route_id=None,
        label=RouteLabel.ORIGINAL,
        mode=mode,
        geometry=line,
        segments=[
            RouteSegment(
                index=0,
                mode=mode,
                geometry=line,
                distance_m=dist_m,
                duration_seconds=duration_s,
                eta_start=departure_time,
                eta_end=departure_time + timedelta(seconds=duration_s),
                instruction=None,
            )
        ],
        distance_m=dist_m,
        duration_seconds=duration_s,
        transfers=0,
        quality=DataQuality(
            status=DataStatus.PARTIAL,
            score=0.45,
            flags=[QualityFlag.INFERRED, QualityFlag.OUTSIDE_COVERAGE],
            coverage=0.0,
            completeness=0.5,
            freshness_seconds=0,
            notes=[note],
        ),
        sources=[src],
    )
