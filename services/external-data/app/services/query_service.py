"""Combined context query: fan-out to providers with bounded concurrency and an overall deadline.

Each capability is independent: a failing provider becomes an entry in ``degraded_services`` or
``unavailable_capabilities`` and never fabricates data. Nothing here decides risk.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4

from sta_common.cache import stable_hash
from sta_common.context import get_context
from sta_common.logging import get_logger
from sta_common.metrics import DEGRADED_RESULTS
from sta_contracts.enums import DataStatus, QualityFlag, SourceAuthority, TransportServiceStatus, TravelMode
from sta_contracts.geo import BBox, Point
from sta_contracts.models import (
    ContextQuery,
    DataQuality,
    DisasterEvent,
    ExternalContext,
    ProviderHealth,
    RouteCandidate,
    TransportStatus,
    WeatherForecastPoint,
)

from app.adapters.amadeus import AmadeusAdapter
from app.adapters.base import BaseAdapter, ProviderError, ProviderErrorCode
from app.adapters.eonet import EonetAdapter
from app.adapters.gdacs import GdacsAdapter
from app.adapters.gtfs import GtfsRealtimeAdapter
from app.adapters.open_meteo_geocoding import OpenMeteoGeocodingAdapter
from app.adapters.open_meteo_weather import OpenMeteoWeatherAdapter
from app.adapters.openrouteservice import PROFILE_BY_MODE, OpenRouteServiceAdapter
from app.adapters.usgs import UsgsAdapter
from app.cache.provider_cache import NegativeCachedError, ProviderCache
from app.domain.canonical import deterministic_id, provenance
from app.repositories.provider_repo import ProviderRepository
from app.services.dedup import dedup_events
from app.services.geodesic import geodesic_route
from app.services.route_sampling import sample_route
from app.settings import Settings

log = get_logger("query-service")
T = TypeVar("T")


@dataclass
class Adapters:
    geocoding: OpenMeteoGeocodingAdapter
    weather: OpenMeteoWeatherAdapter
    ors: OpenRouteServiceAdapter
    usgs: UsgsAdapter
    gdacs: GdacsAdapter
    eonet: EonetAdapter
    gtfs: GtfsRealtimeAdapter
    amadeus: AmadeusAdapter

    def all(self) -> list[BaseAdapter]:
        return [self.geocoding, self.weather, self.ors, self.usgs, self.gdacs, self.eonet, self.gtfs, self.amadeus]

    async def close(self) -> None:
        for a in self.all():
            close = getattr(a, "close", None)
            if close:
                await close()


@dataclass
class _Outcome:
    degraded: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)


class QueryService:
    def __init__(self, settings: Settings, adapters: Adapters, cache: ProviderCache, repo: ProviderRepository) -> None:
        self.s = settings
        self.a = adapters
        self.cache = cache
        self.repo = repo
        self._sem = asyncio.Semaphore(settings.provider_max_concurrency)

    # ------------------------------------------------------------------ helpers
    def _bbox(self, origin: Point, destination: Point, routes: list[RouteCandidate]) -> BBox:
        lons = [origin.lon, destination.lon]
        lats = [origin.lat, destination.lat]
        for r in routes:
            for c in r.geometry.coordinates:
                lons.append(c[0])
                lats.append(c[1])
        b = self.s.disaster_bbox_buffer_deg
        return BBox(
            min_lon=max(-180.0, min(lons) - b),
            min_lat=max(-90.0, min(lats) - b),
            max_lon=min(180.0, max(lons) + b),
            max_lat=min(90.0, max(lats) + b),
        )

    async def _guarded(self, name: str, coro: Awaitable[T], outcome: _Outcome) -> T | None:
        """Run one provider call; classify failures; never raise."""
        started = time.perf_counter()
        ctx = get_context()
        try:
            async with self._sem:
                result = await coro
            await self.repo.log_fetch(
                provider=name,
                request_id=ctx.request_id,
                query_hash="-",
                outcome="ok",
                error_code=None,
                record_count=len(result) if isinstance(result, list) else 1,
                from_cache=False,
                latency_ms=(time.perf_counter() - started) * 1000,
                expires_at=None,
                content_hash=None,
                quality=None,
            )
            return result
        except NegativeCachedError as exc:
            outcome.degraded.append(f"{name}: recent failure cached ({exc})")
            DEGRADED_RESULTS.labels(self.s.service_name, name).inc()
            return None
        except ProviderError as exc:
            if exc.code in (
                ProviderErrorCode.NOT_CONFIGURED,
                ProviderErrorCode.OUTSIDE_COVERAGE,
                ProviderErrorCode.LICENSE_RESTRICTION,
            ):
                outcome.unavailable.append(f"{name}: {exc.message}")
            else:
                outcome.degraded.append(f"{name}: {exc.code.value}")
                DEGRADED_RESULTS.labels(self.s.service_name, name).inc()
            await self.repo.log_fetch(
                provider=name,
                request_id=ctx.request_id,
                query_hash="-",
                outcome="error",
                error_code=exc.code.value,
                record_count=0,
                from_cache=False,
                latency_ms=(time.perf_counter() - started) * 1000,
                expires_at=None,
                content_hash=None,
                quality=None,
            )
            log.warning("provider_failed", provider=name, error_code=exc.code.value)
            return None
        except TimeoutError:
            outcome.degraded.append(f"{name}: PROVIDER_TIMEOUT")
            DEGRADED_RESULTS.labels(self.s.service_name, name).inc()
            return None
        except Exception as exc:  # noqa: BLE001 - unexpected adapter bug must degrade, not crash the context
            log.exception("provider_unexpected_error", provider=name, error_type=type(exc).__name__)
            outcome.degraded.append(f"{name}: INTERNAL_ERROR")
            DEGRADED_RESULTS.labels(self.s.service_name, name).inc()
            return None

    # ------------------------------------------------------------------ capabilities
    async def routes(self, q: ContextQuery, outcome: _Outcome, deadline: float) -> list[RouteCandidate]:
        out: list[RouteCandidate] = []
        for mode in q.travel_modes:
            origin, dest = q.origin.coordinates, q.destination.coordinates
            candidates: list[RouteCandidate] | None = None
            if mode in PROFILE_BY_MODE and self.a.ors.descriptor.enabled:
                query_key = {
                    "o": origin.coordinates,
                    "d": dest.coordinates,
                    "mode": mode.value,
                    "avoid": [p.coordinates for p in q.avoid_geometries],
                    "dep_hour": q.departure_time.astimezone(UTC).strftime("%Y-%m-%dT%H"),
                }

                async def fetch(m: TravelMode = mode, o: Point = origin, d: Point = dest) -> list[RouteCandidate]:
                    return await self.a.ors.directions(
                        o,
                        d,
                        m,
                        departure_time=q.departure_time,
                        avoid_polygons=q.avoid_geometries,
                        deadline=deadline,
                    )

                async def cached(k: dict[str, Any] = query_key, f: Any = fetch) -> list[RouteCandidate]:
                    records, _ = await self.cache.get_or_fetch(
                        provider="openrouteservice",
                        query=k,
                        ttl_seconds=self.s.ttl_route,
                        model=RouteCandidate,
                        fetch=f,
                    )
                    return records

                candidates = await self._guarded("openrouteservice", cached(), outcome)
            elif mode in PROFILE_BY_MODE:
                outcome.unavailable.append(
                    f"openrouteservice: ORS_API_KEY not configured ({mode.value} routing approximated)"
                )
            else:
                outcome.unavailable.append(
                    f"route provider: no real {mode.value} route provider registered (geometry approximated)"
                )
            if not candidates:
                reason = (
                    "no route provider coverage"
                    if mode not in PROFILE_BY_MODE or not self.a.ors.descriptor.enabled
                    else "route provider failed"
                )
                candidates = [
                    geodesic_route(
                        origin, dest, mode, departure_time=q.departure_time, reason=reason, ttl_seconds=self.s.ttl_route
                    )
                ]
            out.extend(candidates)
        return out

    async def weather(
        self, q: ContextQuery, routes: list[RouteCandidate], outcome: _Outcome, deadline: float
    ) -> list[WeatherForecastPoint]:
        if not routes:
            return []
        primary = routes[0]
        samples, coverage = sample_route(
            primary,
            departure_time=q.departure_time,
            max_samples=min(q.max_weather_samples, self.s.route_max_weather_samples),
        )
        query_key = {"samples": [(s.point.coordinates, s.eta_at.strftime("%Y-%m-%dT%H")) for s in samples]}

        async def fetch() -> list[WeatherForecastPoint]:
            return await self.a.weather.forecast_for_samples(samples, deadline=deadline)

        async def cached() -> list[WeatherForecastPoint]:
            records: list[WeatherForecastPoint]
            records, _ = await self.cache.get_or_fetch(
                provider="open_meteo",
                query=query_key,
                ttl_seconds=self.s.ttl_hourly_forecast,
                model=WeatherForecastPoint,
                fetch=fetch,
            )
            return records

        records = await self._guarded("open_meteo", cached(), outcome)
        if not records:
            return []
        for r in records:
            r.quality.coverage = coverage
            if coverage < 1.0:
                r.quality.notes.append(f"route sampled at {len(samples)} points (coverage {coverage:.2f})")
        return records

    async def disasters(
        self, q: ContextQuery, routes: list[RouteCandidate], outcome: _Outcome, deadline: float
    ) -> tuple[list[DisasterEvent], BBox]:
        bbox = self._bbox(q.origin.coordinates, q.destination.coordinates, routes)
        key = {"bbox": [round(v, 2) for v in bbox.as_list()], "days": self.s.disaster_lookback_days}

        def cached(provider: str, adapter: Any, ttl: int) -> Awaitable[list[DisasterEvent]]:
            async def fetch() -> list[DisasterEvent]:
                result: list[DisasterEvent] = await adapter.query(
                    bbox, lookback_days=self.s.disaster_lookback_days, deadline=deadline
                )
                return result

            async def run() -> list[DisasterEvent]:
                records: list[DisasterEvent]
                records, _ = await self.cache.get_or_fetch(
                    provider=provider, query=key, ttl_seconds=ttl, model=DisasterEvent, fetch=fetch
                )
                return records

            return run()

        results = await asyncio.gather(
            self._guarded("usgs", cached("usgs", self.a.usgs, self.s.ttl_disaster), outcome),
            self._guarded("gdacs", cached("gdacs", self.a.gdacs, self.s.ttl_disaster), outcome),
            self._guarded("eonet", cached("eonet", self.a.eonet, self.s.ttl_disaster), outcome),
        )
        events: list[DisasterEvent] = []
        for r in results:
            if r:
                events.extend(r)
        return dedup_events(events), bbox

    async def transport(self, q: ContextQuery, outcome: _Outcome, deadline: float) -> list[TransportStatus]:
        out: list[TransportStatus] = []
        fetched_at = datetime.now(UTC)
        for mode in q.travel_modes:
            if mode in (TravelMode.TRAIN, TravelMode.BUS, TravelMode.MULTIMODAL):
                key = {
                    "o": q.origin.coordinates.coordinates,
                    "d": q.destination.coordinates.coordinates,
                    "mode": mode.value,
                }

                async def fetch(m: TravelMode = mode) -> list[TransportStatus]:
                    return await self.a.gtfs.status(
                        q.origin.coordinates, q.destination.coordinates, m, deadline=deadline
                    )

                async def cached(k: dict[str, Any] = key, f: Any = fetch) -> list[TransportStatus]:
                    records, _ = await self.cache.get_or_fetch(
                        provider="gtfs_rt", query=k, ttl_seconds=self.s.ttl_gtfs_rt, model=TransportStatus, fetch=f
                    )
                    return records

                records = await self._guarded("gtfs_rt", cached(), outcome)
                if records:
                    out.extend(records)
                else:
                    out.append(self._unknown_transport(mode, fetched_at, "no real-time transit feed covers this trip"))
            elif mode == TravelMode.FLIGHT:
                if self.a.amadeus.descriptor.enabled:
                    outcome.unavailable.append(
                        "amadeus: flight status requires carrier + flight number (not provided in trip)"
                    )
                else:
                    outcome.unavailable.append("amadeus: production credentials not configured")
                out.append(self._unknown_transport(mode, fetched_at, "flight operational status unavailable"))
        return out

    def _unknown_transport(self, mode: TravelMode, fetched_at: datetime, reason: str) -> TransportStatus:
        return TransportStatus(
            id=deterministic_id("transport-unknown", mode.value, fetched_at.strftime("%Y%m%d%H")),
            mode=mode,
            status=TransportServiceStatus.UNKNOWN,
            message=reason,
            quality=DataQuality(
                status=DataStatus.UNAVAILABLE,
                score=0.0,
                flags=[QualityFlag.OUTSIDE_COVERAGE, QualityFlag.MISSING],
                coverage=0.0,
                notes=[reason],
            ),
            source=provenance(
                provider="none",
                record_id=None,
                authority=SourceAuthority.UNKNOWN,
                source_url=None,
                license_=None,
                attribution=None,
                observed_at=None,
                published_at=None,
                fetched_at=fetched_at,
                ttl_seconds=self.s.ttl_gtfs_rt,
                raw={"mode": mode.value, "reason": reason},
            ),
        )

    # ------------------------------------------------------------------ entry point
    async def context(self, q: ContextQuery) -> ExternalContext:
        deadline = self.s.context_deadline_seconds
        outcome = _Outcome()
        routes: list[RouteCandidate] = []
        if "routes" in q.include:
            routes = await self.routes(q, outcome, deadline)
        weather_task = self.weather(q, routes, outcome, deadline) if "weather" in q.include else _empty()
        disaster_task = (
            self.disasters(q, routes, outcome, deadline)
            if "disasters" in q.include
            else _empty_bbox(self._bbox(q.origin.coordinates, q.destination.coordinates, routes))
        )
        transport_task = self.transport(q, outcome, deadline) if "transport" in q.include else _empty()
        weather, (events, bbox), transport = await asyncio.gather(weather_task, disaster_task, transport_task)
        official = [e for e in events if e.official]
        health = [a.provider_health() for a in self.a.all()]
        await self.repo.record_health(health)
        return ExternalContext(
            context_id=uuid4(),
            request_id=q.request_id,
            routes=routes,
            weather=weather,
            transport=transport,
            disaster_events=events,
            official_alerts=official,
            provider_health=health,
            degraded_services=sorted(set(outcome.degraded)),
            unavailable_capabilities=sorted(set(outcome.unavailable)),
            fetched_at=datetime.now(UTC),
            bbox=bbox,
        )

    def health(self) -> list[ProviderHealth]:
        return [a.provider_health() for a in self.a.all()]


async def _empty() -> list[Any]:
    return []


async def _empty_bbox(b: BBox) -> tuple[list[DisasterEvent], BBox]:
    return [], b


def query_hash(payload: Any) -> str:
    return stable_hash(payload)
