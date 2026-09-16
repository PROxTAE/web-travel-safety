"""Real-provider canary (04 plan Phase 7 / runbook §7).

Usage:
  uv run python -m app.cli.canary                 # print canary report (JSON)
  uv run python -m app.cli.canary --capture DIR   # also write sanitized raw fixtures with provenance metadata

Calls every enabled provider once with a tiny query, validates the canonical schema, and reports
source/fetched/expires timestamps. Disabled providers report UNAVAILABLE honestly.
Never run this inside unit tests; it is quota-bearing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sta_contracts.enums import TravelMode
from sta_contracts.geo import BBox, Point

from app.adapters.base import ProviderError
from app.adapters.open_meteo_weather import HOURLY_FIELDS, WeatherSample
from app.main import build_adapters
from app.settings import get_settings

BANGKOK = Point(coordinates=[100.5018, 13.7563])
CHIANG_MAI = Point(coordinates=[98.9853, 18.7883])
BOSTON = Point(coordinates=[-71.0589, 42.3601])
CAMBRIDGE = Point(coordinates=[-71.1097, 42.3736])


async def _raw_get(url: str, params: dict[str, Any] | None = None, *, binary: bool = False) -> Any:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        return r.content if binary else r.json()


def _fixture(source: str, license_: str, redaction: str, data: Any) -> dict[str, Any]:
    return {
        "_fixture": {
            "source": source,
            "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "schema_version": "1.0.0",
            "license": license_,
            "redaction": redaction,
        },
        "data": data,
    }


async def run(capture: Path | None) -> dict[str, Any]:
    s = get_settings()
    a = build_adapters(s)
    report: dict[str, Any] = {"ran_at": datetime.now(UTC).isoformat(), "providers": {}}
    dep = datetime.now(UTC) + timedelta(hours=6)

    async def step(name: str, fn: Any) -> None:
        try:
            out = await fn()
            report["providers"][name] = {"status": "OK", **out}
        except ProviderError as exc:
            report["providers"][name] = {
                "status": "UNAVAILABLE" if exc.code.value in ("NOT_CONFIGURED", "OUTSIDE_COVERAGE") else "ERROR",
                "code": exc.code.value,
                "message": exc.message,
            }
        except Exception as exc:  # noqa: BLE001
            report["providers"][name] = {"status": "ERROR", "error_type": type(exc).__name__, "message": str(exc)[:200]}

    async def geocode() -> dict[str, Any]:
        res = await a.geocoding.search("Chiang Mai", locale="en")
        if capture:
            raw = await _raw_get(
                f"{s.open_meteo_geocoding_url}/v1/search",
                {"name": "Chiang Mai", "count": 3, "language": "en", "format": "json"},
            )
            (capture / "open_meteo_geocoding.chiang_mai.json").write_text(
                json.dumps(
                    _fixture(
                        "https://geocoding-api.open-meteo.com/v1/search?name=Chiang+Mai&count=3",
                        "CC BY 4.0",
                        "none; public places",
                        raw,
                    ),
                    indent=2,
                ),
                encoding="utf-8",
            )
        return {"results": len(res), "first": res[0].model_dump(mode="json") if res else None}

    async def weather() -> dict[str, Any]:
        samples = [
            WeatherSample(index=0, point=BANGKOK, eta_at=dep),
            WeatherSample(index=1, point=CHIANG_MAI, eta_at=dep + timedelta(hours=8)),
        ]
        res = await a.weather.forecast_for_samples(samples)
        cur = await a.weather.current(BANGKOK)
        if capture:
            raw = await _raw_get(
                f"{s.open_meteo_base_url}/v1/forecast",
                {
                    "latitude": "13.7563,18.7883",
                    "longitude": "100.5018,98.9853",
                    "hourly": ",".join(HOURLY_FIELDS),
                    "timezone": "UTC",
                    "start_date": dep.date().isoformat(),
                    "end_date": (dep + timedelta(days=1)).date().isoformat(),
                },
            )
            (capture / "open_meteo_forecast.bkk_cnx.json").write_text(
                json.dumps(
                    _fixture(
                        "https://api.open-meteo.com/v1/forecast (2 locations, hourly)",
                        "CC BY 4.0",
                        "none; model forecast at city centroids",
                        raw,
                    ),
                    indent=2,
                ),
                encoding="utf-8",
            )
        return {
            "points": len(res),
            "current": {
                "valid_at": cur.valid_at.isoformat(),
                "temperature_c": cur.temperature_c,
                "severity": cur.severity,
                "expires_at": cur.source.expires_at.isoformat() if cur.source.expires_at else None,
            },
        }

    async def usgs() -> dict[str, Any]:
        bbox = BBox(min_lon=95, min_lat=5, max_lon=110, max_lat=22)
        res = await a.usgs.query(bbox, lookback_days=30)
        if capture:
            raw = await _raw_get(
                s.usgs_query_url,
                {
                    "format": "geojson",
                    "starttime": (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%d"),
                    "minlatitude": 5,
                    "maxlatitude": 22,
                    "minlongitude": 95,
                    "maxlongitude": 110,
                    "minmagnitude": 2.5,
                    "limit": 5,
                },
            )
            (capture / "usgs.southeast_asia.json").write_text(
                json.dumps(
                    _fixture(
                        "https://earthquake.usgs.gov/fdsnws/event/1/query (SE Asia bbox, 30d, M2.5+, limit 5)",
                        "public domain",
                        "none; official events",
                        raw,
                    ),
                    indent=2,
                ),
                encoding="utf-8",
            )
        return {
            "events": len(res),
            "first": res[0].model_dump(mode="json", include={"event_id", "severity", "magnitude", "effective_at"})
            if res
            else None,
        }

    async def gdacs() -> dict[str, Any]:
        bbox = BBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90)
        res = await a.gdacs.query(bbox, lookback_days=7)
        if capture:
            now = datetime.now(UTC)
            raw = await _raw_get(
                f"{s.gdacs_base_url}/api/events/geteventlist/SEARCH",
                {
                    "fromDate": (now - timedelta(days=7)).date().isoformat(),
                    "toDate": now.date().isoformat(),
                    "alertlevel": "Green;Orange;Red",
                    "eventlist": "EQ;TC;FL;VO;WF;DR",
                },
            )
            raw["features"] = raw["features"][:5]
            (capture / "gdacs.eventlist.json").write_text(
                json.dumps(
                    _fixture(
                        "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH (7d, truncated to 5 features)",
                        "GDACS terms (attribution)",
                        "truncated to 5 features",
                        raw,
                    ),
                    indent=2,
                ),
                encoding="utf-8",
            )
        return {"events": len(res), "official": sum(1 for e in res if e.official)}

    async def eonet() -> dict[str, Any]:
        bbox = BBox(min_lon=-180, min_lat=-60, max_lon=180, max_lat=80)
        res = await a.eonet.query(bbox, lookback_days=20)
        if capture:
            raw = await _raw_get(f"{s.eonet_base_url}/events", {"status": "open", "days": 20, "limit": 3})
            (capture / "eonet.open_events.json").write_text(
                json.dumps(
                    _fixture(
                        "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&days=20&limit=3",
                        "NASA public domain",
                        "limit 3",
                        raw,
                    ),
                    indent=2,
                ),
                encoding="utf-8",
            )
        return {"events": len(res)}

    async def gtfs() -> dict[str, Any]:
        res = await a.gtfs.status(BOSTON, CAMBRIDGE, TravelMode.TRAIN)
        if capture:
            pb = await _raw_get("https://cdn.mbta.com/realtime/Alerts.pb", binary=True)
            (capture / "mbta.alerts.pb").write_bytes(pb)
            (capture / "mbta.alerts.pb.meta.json").write_text(
                json.dumps(
                    _fixture(
                        "https://cdn.mbta.com/realtime/Alerts.pb",
                        "MBTA developer license",
                        "binary protobuf as served (no PII in feed)",
                        {"bytes": len(pb)},
                    ),
                    indent=2,
                ),
                encoding="utf-8",
            )
        return {"records": len(res), "statuses": sorted({r.status.value for r in res})}

    async def ors() -> dict[str, Any]:
        res = await a.ors.directions(BANGKOK, Point(coordinates=[100.75, 13.69]), TravelMode.CAR, departure_time=dep)
        return {"routes": len(res), "distance_m": res[0].distance_m if res else None}

    async def amadeus() -> dict[str, Any]:
        res = await a.amadeus.flight_status("TG", "102", (datetime.now(UTC) + timedelta(days=3)).date().isoformat())
        return {"records": len(res)}

    if capture:
        await asyncio.to_thread(capture.mkdir, parents=True, exist_ok=True)
    await step("open_meteo_geocoding", geocode)
    await step("open_meteo", weather)
    await step("usgs", usgs)
    await step("gdacs", gdacs)
    await step("eonet", eonet)
    await step("gtfs_rt", gtfs)
    await step("openrouteservice", ors)
    await step("amadeus", amadeus)
    await a.close()
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", type=Path, default=None)
    args = ap.parse_args()
    report = asyncio.run(run(args.capture))
    print(json.dumps(report, indent=2, default=str))
    bad = [k for k, v in report["providers"].items() if v["status"] == "ERROR"]
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
