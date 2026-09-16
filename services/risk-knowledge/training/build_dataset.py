"""Build the historical training dataset from REAL sources (06 plan Phase 2).

For each route × day in ``training/routes.yaml``:
  features  <- day-1 FORECAST at corridor sample points (Open-Meteo Previous Runs API) + USGS quakes of the
               previous 24 h + route geometry, passed through data-integration ``POST /internal/v1/snapshots``
               so online and offline features are produced by the same code path
  label     <- OBSERVED outcome (Open-Meteo Archive actuals during the travel window, GDACS Orange/Red official
               events on the corridor, USGS M>=5.5 during the window) per training/label_policy.yaml

Output: data/dataset.parquet + data/dataset.manifest.json (sources, windows, checksums, label policy, split).
Raw provider responses are cached under data/raw/ (gitignored) for reproducibility.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
import polars as pl
import yaml
from pyproj import Geod
from sta_contracts.enums import DisasterEventType, RouteLabel, Severity, SourceAuthority, TravelMode
from sta_contracts.geo import LineString, Point
from sta_contracts.models import (
    DataQuality,
    DisasterEvent,
    ExternalContext,
    LocationRef,
    ProviderHealth,
    RouteCandidate,
    RouteSegment,
    SnapshotCreateRequest,
    SourceProvenance,
    TravelRequest,
    WeatherForecastPoint,
)

ROOT = Path(__file__).resolve().parents[1]
GEOD = Geod(ellps="WGS84")
PREV_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
USGS = "https://earthquake.usgs.gov/fdsnws/event/1/query"
GDACS = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
FORECAST_VARS = [
    "temperature_2m",
    "precipitation",
    "precipitation_probability",
    "snowfall",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
    "visibility",
]
ACTUAL_VARS = ["precipitation", "wind_gusts_10m", "weather_code"]
SPEED_KMH = {TravelMode.FLIGHT: 750.0, TravelMode.TRAIN: 80.0, TravelMode.BUS: 60.0, TravelMode.CAR: 70.0}
WMO_SEV = {95: 4, 96: 5, 99: 5, 65: 4, 66: 4, 67: 4, 75: 4, 82: 4, 86: 4, 63: 3, 73: 3, 81: 3, 85: 3, 56: 3, 57: 3}


def _cache_get(client: httpx.Client, raw_dir: Path, url: str, params: dict[str, Any]) -> Any:
    key = hashlib.sha256((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:24]
    p = raw_dir / f"{key}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    for attempt in range(4):
        r = client.get(url, params=params)
        if r.status_code == 429:
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        data = r.json()
        p.write_text(
            json.dumps(
                {"_source": url, "_params": params, "_captured_at": datetime.now(UTC).isoformat(), "data": data}
            ),
            encoding="utf-8",
        )
        time.sleep(0.4)  # be polite to free APIs
        return data
    raise RuntimeError(f"rate limited: {url}")


def _unwrap(x: Any) -> Any:
    return x["data"] if isinstance(x, dict) and "data" in x and "_source" in x else x


def geocode(client: httpx.Client, raw: Path, name: str, country: str) -> LocationRef:
    d = _unwrap(_cache_get(client, raw, GEOCODE, {"name": name, "count": 1, "language": "en", "countryCode": country}))
    p = d["results"][0]
    return LocationRef(
        place_id=str(p["id"]),
        display_name=f"{p['name']}, {p.get('country', country)}",
        coordinates=Point(coordinates=[p["longitude"], p["latitude"]]),
        country_code=country,
        admin1=p.get("admin1"),
        timezone=p["timezone"],
        provider="open_meteo_geocoding",
        confirmed_by_user=True,
    )


def geodesic_points(a: Point, b: Point, n: int) -> tuple[list[list[float]], float]:
    _, _, d = GEOD.inv(a.lon, a.lat, b.lon, b.lat)
    inner = GEOD.npts(a.lon, a.lat, b.lon, b.lat, n - 2)
    return [[a.lon, a.lat], *[[float(x), float(y)] for x, y in inner], [b.lon, b.lat]], float(d)


def _src(
    provider: str, rid: str, fetched: datetime, authority: SourceAuthority, raw: Any, url: str
) -> SourceProvenance:
    return SourceProvenance(
        source_id=uuid5(NAMESPACE_URL, f"train:{provider}:{rid}"),
        provider=provider,
        provider_record_id=rid,
        authority=authority,
        source_url=url,
        license="see training manifest",
        observed_at=fetched,
        fetched_at=fetched,
        expires_at=fetched + timedelta(hours=1),
        content_hash=hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest(),
    )


def build_route(
    route_cfg: dict[str, Any], origin: LocationRef, dest: LocationRef, dep: datetime, n_points: int
) -> RouteCandidate:
    mode = TravelMode(route_cfg["mode"])
    coords, dist = geodesic_points(origin.coordinates, dest.coordinates, max(n_points, 3))
    dur = dist / 1000 / SPEED_KMH[mode] * 3600 + (120 * 60 if mode == TravelMode.FLIGHT else 20 * 60)
    line = LineString(coordinates=coords)
    return RouteCandidate(
        route_id=uuid5(NAMESPACE_URL, f"train-route:{route_cfg['id']}"),
        label=RouteLabel.ORIGINAL,
        mode=mode,
        geometry=line,
        segments=[
            RouteSegment(
                index=0,
                mode=mode,
                geometry=line,
                distance_m=dist,
                duration_seconds=dur,
                eta_start=dep,
                eta_end=dep + timedelta(seconds=dur),
            )
        ],
        distance_m=dist,
        duration_seconds=dur,
        quality=DataQuality(score=0.45, flags=["INFERRED", "OUTSIDE_COVERAGE"], freshness_seconds=0),
        sources=[_src("geodesic", route_cfg["id"], dep, SourceAuthority.UNKNOWN, coords, "computed")],
    )


def fetch_hourly(
    client: httpx.Client, raw: Path, url: str, coords: list[list[float]], start: date, end: date, hourly: list[str]
) -> list[dict[str, Any]]:
    params = {
        "latitude": ",".join(f"{c[1]:.4f}" for c in coords),
        "longitude": ",".join(f"{c[0]:.4f}" for c in coords),
        "hourly": ",".join(hourly),
        "timezone": "UTC",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "wind_speed_unit": "kmh",
    }
    d = _unwrap(_cache_get(client, raw, url, params))
    return d if isinstance(d, list) else [d]


def slot(series: dict[str, Any], key: str, t: datetime) -> Any:
    times = series["time"]
    ts = t.strftime("%Y-%m-%dT%H:00")
    try:
        i = times.index(ts)
    except ValueError:
        return None
    vals = series.get(key)
    return None if vals is None else vals[i]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--routes", type=Path, default=ROOT / "training" / "routes.yaml")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "dataset.parquet")
    ap.add_argument(
        "--integration-url", default=os.environ.get("DATA_INTEGRATION_SERVICE_URL", "http://localhost:18003")
    )
    ap.add_argument("--service-token", default=os.environ.get("SERVICE_AUTH_TOKEN", ""))
    ap.add_argument("--limit-days", type=int, default=None)
    ap.add_argument("--day-step", type=int, default=1)
    args = ap.parse_args()

    cfg = yaml.safe_load(args.routes.read_text(encoding="utf-8"))
    policy = yaml.safe_load((ROOT / "training" / "label_policy.yaml").read_text(encoding="utf-8"))
    raw = ROOT / "data" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    start, end = date.fromisoformat(cfg["period"]["start"]), date.fromisoformat(cfg["period"]["end"])
    days = [start + timedelta(days=i) for i in range(0, (end - start).days + 1, args.day_step)]
    if args.limit_days:
        days = days[: args.limit_days]
    n_pts = int(cfg["sample_points_per_route"])
    dep_hour = int(cfg["departure_local_hour"])
    headers = {"Authorization": f"Bearer {args.service_token}"} if args.service_token else {}
    rows: list[dict[str, Any]] = []
    stats = {"routes": 0, "days": len(days), "samples": 0, "positives": 0, "snapshot_errors": 0, "api_calls": 0}
    pos_if = policy["positive_if_any"]
    with (
        httpx.Client(timeout=60, headers={"User-Agent": "smart-travel-assistant/training"}) as client,
        httpx.Client(timeout=60, headers=headers, base_url=args.integration_url) as integ,
    ):
        # official events once for the whole period (GDACS Orange/Red)
        gd = _unwrap(
            _cache_get(
                client,
                raw,
                GDACS,
                {
                    "fromDate": start.isoformat(),
                    "toDate": end.isoformat(),
                    "alertlevel": "Orange;Red",
                    "eventlist": "EQ;TC;FL;VO;WF;DR",
                },
            )
        )
        gdacs_events = []
        for f in gd.get("features", []):
            p = f["properties"]
            c = f["geometry"]["coordinates"]
            gdacs_events.append(
                {
                    "id": f"gdacs:{p['eventtype']}:{p['eventid']}:{p.get('episodeid', 0)}",
                    "type": {
                        "EQ": "EARTHQUAKE",
                        "TC": "CYCLONE",
                        "FL": "FLOOD",
                        "VO": "VOLCANO",
                        "WF": "WILDFIRE",
                        "DR": "EXTREME_TEMPERATURE",
                    }.get(p["eventtype"], "OTHER"),
                    "lon": c[0],
                    "lat": c[1],
                    "from": p.get("fromdate"),
                    "to": p.get("todate"),
                    "level": (p.get("episodealertlevel") or p.get("alertlevel") or "").lower(),
                    "name": p.get("name") or p.get("description") or "",
                    "raw": p,
                }
            )
        for rc in cfg["routes"]:
            stats["routes"] += 1
            origin = geocode(client, raw, rc["origin"], rc["country"])
            dest = geocode(client, raw, rc["destination"], rc["country"])
            probe_route = build_route(rc, origin, dest, datetime.now(UTC), n_pts)
            coords = probe_route.geometry.coordinates
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(origin.timezone)
            # bbox for quakes
            lons = [c[0] for c in coords]
            lats = [c[1] for c in coords]
            usgs = _unwrap(
                _cache_get(
                    client,
                    raw,
                    USGS,
                    {
                        "format": "geojson",
                        "starttime": (start - timedelta(days=1)).isoformat(),
                        "endtime": (end + timedelta(days=1)).isoformat(),
                        "minlatitude": min(lats) - 1.5,
                        "maxlatitude": max(lats) + 1.5,
                        "minlongitude": min(lons) - 1.5,
                        "maxlongitude": max(lons) + 1.5,
                        "minmagnitude": 4.0,
                        "limit": 20000,
                    },
                )
            )
            quakes = [
                {
                    "id": f["id"],
                    "mag": f["properties"]["mag"],
                    "t": datetime.fromtimestamp(f["properties"]["time"] / 1000, tz=UTC),
                    "lon": f["geometry"]["coordinates"][0],
                    "lat": f["geometry"]["coordinates"][1],
                    "url": f["properties"].get("url"),
                }
                for f in usgs.get("features", [])
            ]
            # monthly chunks for hourly APIs
            month_starts = sorted({date(d.year, d.month, 1) for d in days})
            fc_by_month: dict[date, list[dict[str, Any]]] = {}
            ac_by_month: dict[date, list[dict[str, Any]]] = {}
            for ms in month_starts:
                me = (ms.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
                me = min(me, end)
                fc_by_month[ms] = fetch_hourly(
                    client, raw, PREV_RUNS, coords, ms, me, [f"{v}_previous_day1" for v in FORECAST_VARS]
                )
                ac_by_month[ms] = fetch_hourly(client, raw, ARCHIVE, coords, ms, me, ACTUAL_VARS)
                stats["api_calls"] += 2
            for day in days:
                ms = date(day.year, day.month, 1)
                dep_local = datetime(day.year, day.month, day.day, dep_hour, tzinfo=tz)
                dep = dep_local.astimezone(UTC)
                route = build_route(rc, origin, dest, dep, n_pts)
                dur = route.duration_seconds
                # forecast weather points at ETA of each sample (planned) — features
                wx_points: list[WeatherForecastPoint] = []
                actual_max_precip = 0.0
                actual_max_gust = 0.0
                actual_codes: set[int] = set()
                for i, c in enumerate(coords):
                    eta = dep + timedelta(seconds=dur * i / (len(coords) - 1))
                    fc = fc_by_month[ms][i]["hourly"]
                    ac = ac_by_month[ms][i]["hourly"]
                    vals = {v: slot(fc, f"{v}_previous_day1", eta) for v in FORECAST_VARS}
                    code = vals["weather_code"]
                    sev_rank = WMO_SEV.get(int(code), 1) if code is not None else 0
                    gust = vals["wind_gusts_10m"]
                    if gust is not None and gust >= 89:
                        sev_rank = max(sev_rank, 4)
                    if vals["precipitation"] is not None and vals["precipitation"] >= 30:
                        sev_rank = max(sev_rank, 4)
                    sev = {
                        0: Severity.UNKNOWN,
                        1: Severity.INFO,
                        2: Severity.MINOR,
                        3: Severity.MODERATE,
                        4: Severity.SEVERE,
                        5: Severity.EXTREME,
                    }[sev_rank]
                    wx_points.append(
                        WeatherForecastPoint(
                            id=uuid5(NAMESPACE_URL, f"train-wx:{rc['id']}:{day}:{i}"),
                            location=Point(coordinates=c),
                            valid_at=eta.replace(minute=0, second=0, microsecond=0),
                            route_sample_index=i,
                            eta_at=eta,
                            temperature_c=vals["temperature_2m"],
                            precipitation_mm=vals["precipitation"],
                            precipitation_probability=vals["precipitation_probability"],
                            snowfall_cm=vals["snowfall"],
                            wind_speed_kmh=vals["wind_speed_10m"],
                            wind_gust_kmh=gust,
                            visibility_m=vals["visibility"],
                            weather_code=int(code) if code is not None else None,
                            severity=sev,
                            quality=DataQuality(score=0.9, completeness=1.0, freshness_seconds=0),
                            source=_src(
                                "open_meteo_previous_runs",
                                f"{rc['id']}:{day}:{i}",
                                dep,
                                SourceAuthority.LICENSED_PROVIDER,
                                vals,
                                PREV_RUNS,
                            ),
                        )
                    )
                    # actuals during the window around this sample's ETA (±1 h)
                    for h in (-1, 0, 1):
                        t = eta + timedelta(hours=h)
                        ap_ = slot(ac, "precipitation", t)
                        ag = slot(ac, "wind_gusts_10m", t)
                        acode = slot(ac, "weather_code", t)
                        if ap_ is not None:
                            actual_max_precip = max(actual_max_precip, float(ap_))
                        if ag is not None:
                            actual_max_gust = max(actual_max_gust, float(ag))
                        if acode is not None:
                            actual_codes.add(int(acode))
                # quakes in previous 24h (feature) and during window (label)
                events: list[DisasterEvent] = []
                label_quake = False
                for q in quakes:
                    if dep - timedelta(hours=24) <= q["t"] <= dep + timedelta(seconds=dur):
                        events.append(
                            DisasterEvent(
                                event_id=f"usgs:{q['id']}",
                                event_type=DisasterEventType.EARTHQUAKE,
                                title=f"M {q['mag']} earthquake",
                                severity=Severity.SEVERE
                                if q["mag"] >= 6
                                else Severity.MODERATE
                                if q["mag"] >= 5
                                else Severity.MINOR,
                                magnitude=q["mag"],
                                geometry=Point(coordinates=[q["lon"], q["lat"]]),
                                effective_at=q["t"],
                                official=True,
                                quality=DataQuality(score=0.95, freshness_seconds=0),
                                source=_src("usgs", q["id"], dep, SourceAuthority.OFFICIAL, q["id"], q["url"] or USGS),
                            )
                        )
                        if q["mag"] >= pos_if["usgs_magnitude_within_corridor"] and q["t"] >= dep:
                            _, _, dkm = GEOD.inv(
                                q["lon"], q["lat"], coords[len(coords) // 2][0], coords[len(coords) // 2][1]
                            )
                            if dkm / 1000 <= 150:
                                label_quake = True
                # GDACS official alerts active that day near corridor (label + feature)
                label_official = False
                for ev in gdacs_events:
                    if not ev["from"]:
                        continue
                    f0 = datetime.fromisoformat(ev["from"]).replace(tzinfo=UTC)
                    t0 = datetime.fromisoformat(ev["to"]).replace(tzinfo=UTC) if ev["to"] else f0 + timedelta(days=3)
                    if not (f0 <= dep + timedelta(seconds=dur) and t0 >= dep):
                        continue
                    dmin = min(GEOD.inv(ev["lon"], ev["lat"], c[0], c[1])[2] for c in coords) / 1000
                    if dmin <= 150:
                        label_official = True
                        # only alerts published BEFORE departure are visible as features (no leakage)
                        if f0 <= dep:
                            events.append(
                                DisasterEvent(
                                    event_id=ev["id"],
                                    event_type=DisasterEventType(ev["type"]),
                                    title=ev["name"][:300],
                                    severity=Severity.EXTREME if ev["level"] == "red" else Severity.SEVERE,
                                    geometry=Point(coordinates=[ev["lon"], ev["lat"]]),
                                    effective_at=f0,
                                    ends_at=t0,
                                    official=True,
                                    quality=DataQuality(score=0.9, freshness_seconds=0),
                                    source=_src(
                                        "gdacs", ev["id"], dep, SourceAuthority.INTERGOVERNMENTAL, ev["raw"], GDACS
                                    ),
                                )
                            )
                req = TravelRequest(
                    request_id=uuid4(),
                    trip_id=uuid5(NAMESPACE_URL, f"train-trip:{rc['id']}"),
                    origin=origin,
                    destination=dest,
                    departure_time=dep,
                    travel_modes=[route.mode],
                    timezone=origin.timezone,
                )
                ctx = ExternalContext(
                    context_id=uuid4(),
                    request_id=req.request_id,
                    routes=[route],
                    weather=wx_points,
                    transport=[],
                    disaster_events=events,
                    official_alerts=[e for e in events if e.official],
                    provider_health=[
                        ProviderHealth(
                            provider="open_meteo_previous_runs",
                            kind="WEATHER",
                            status="UP",
                            enabled=True,
                            checked_at=dep,
                        )
                    ],
                    fetched_at=dep,
                )
                resp = integ.post(
                    "/internal/v1/snapshots",
                    json=SnapshotCreateRequest(travel_request=req, external_context=ctx).model_dump(mode="json"),
                )
                if resp.status_code != 201:
                    stats["snapshot_errors"] += 1
                    continue
                snap = resp.json()["data"]
                label = int(
                    actual_max_precip >= pos_if["actual_max_precipitation_mm_per_hour"]
                    or actual_max_gust >= pos_if["actual_max_wind_gust_kmh"]
                    or bool(actual_codes & set(pos_if["actual_weather_code_in"]))
                    or label_official
                    or label_quake
                )
                rows.append(
                    {
                        "route_id": rc["id"],
                        "region": rc["region"],
                        "country": rc["country"],
                        "mode": rc["mode"],
                        "date": day.isoformat(),
                        "departure_utc": dep.isoformat(),
                        "label": label,
                        "label_actual_max_precip_mm": actual_max_precip,
                        "label_actual_max_gust_kmh": actual_max_gust,
                        "label_official_alert": int(label_official),
                        "label_quake": int(label_quake),
                        **{k: (float(v) if v is not None else None) for k, v in snap["features"].items()},
                    }
                )
                stats["samples"] += 1
                stats["positives"] += label
            print(
                f"route {rc['id']}: samples so far {stats['samples']} positives {stats['positives']}", file=sys.stderr
            )
    df = pl.DataFrame(rows, infer_schema_length=None)
    df.write_parquet(args.out)
    manifest = {
        "built_at": datetime.now(UTC).isoformat(),
        "period": cfg["period"],
        "routes": [r["id"] for r in cfg["routes"]],
        "split": cfg["split"],
        "label_policy": policy,
        "feature_schema_version": snap["feature_schema_version"] if rows else None,
        "sources": {
            "features_weather": "Open-Meteo Previous Runs API (day-1 forecast) CC BY 4.0",
            "labels_weather": "Open-Meteo Archive API (ERA5) CC BY 4.0",
            "labels_official": "GDACS Orange/Red event list",
            "quakes": "USGS FDSN catalog M>=4.0",
        },
        "stats": stats,
        "dataset_sha256": hashlib.sha256(args.out.read_bytes()).hexdigest(),
        "rows": len(rows),
        "positive_rate": (stats["positives"] / stats["samples"]) if stats["samples"] else None,
    }
    (args.out.with_suffix(".manifest.json")).write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if k != "label_policy"}, indent=2, default=str))


if __name__ == "__main__":
    main()
