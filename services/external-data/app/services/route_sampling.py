"""Sample points along a route geometry with the ETA at each sample (for weather queries).

Sampling is by geodesic distance along the line; ETA is interpolated from cumulative distance
against total duration (segments give a better ETA when present). Point count is capped so that
provider quota is respected; coverage is reported to the caller.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pyproj import Geod
from sta_contracts.geo import LineString, Point
from sta_contracts.models import RouteCandidate

from app.adapters.open_meteo_weather import WeatherSample

GEOD = Geod(ellps="WGS84")


def cumulative_distances(line: LineString) -> list[float]:
    cum = [0.0]
    for (lon1, lat1, *_), (lon2, lat2, *_) in zip(line.coordinates, line.coordinates[1:], strict=False):
        _, _, d = GEOD.inv(lon1, lat1, lon2, lat2)
        cum.append(cum[-1] + d)
    return cum


def sample_route(
    route: RouteCandidate, *, departure_time: datetime, max_samples: int
) -> tuple[list[WeatherSample], float]:
    """Return (samples, coverage_ratio). coverage_ratio = samples / ideal samples at 25 km spacing (capped 1.0)."""
    cum = cumulative_distances(route.geometry)
    total = cum[-1] or 1.0
    ideal = max(2, int(total / 25_000) + 1)
    n = max(2, min(max_samples, ideal))
    coverage = min(1.0, n / ideal)
    targets = [total * i / (n - 1) for i in range(n)]
    samples: list[WeatherSample] = []
    j = 0
    for idx, target in enumerate(targets):
        while j < len(cum) - 2 and cum[j + 1] < target:
            j += 1
        seg_len = cum[j + 1] - cum[j]
        frac = 0.0 if seg_len == 0 else (target - cum[j]) / seg_len
        lon1, lat1 = route.geometry.coordinates[j][:2]
        lon2, lat2 = route.geometry.coordinates[j + 1][:2]
        if frac <= 0:
            lon, lat = lon1, lat1
        elif frac >= 1:
            lon, lat = lon2, lat2
        else:
            az, _, d = GEOD.inv(lon1, lat1, lon2, lat2)
            lon, lat, _ = GEOD.fwd(lon1, lat1, az, d * frac)
        eta = departure_time + timedelta(seconds=route.duration_seconds * (target / total))
        samples.append(WeatherSample(index=idx, point=Point(coordinates=[float(lon), float(lat)]), eta_at=eta))
    return samples, coverage
