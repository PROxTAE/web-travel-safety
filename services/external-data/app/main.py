"""external-data service entry point (คน 4)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine
from sta_common.app import create_app
from sta_common.cache import Cache, make_redis
from sta_common.db import build_dsn, create_engine, ping, session_factory
from sta_common.health import HealthRegistry
from sta_common.internal_auth import InternalAuth
from sta_common.logging import get_logger

from app.adapters.amadeus import AmadeusAdapter
from app.adapters.eonet import EonetAdapter
from app.adapters.gdacs import GdacsAdapter
from app.adapters.gtfs import GtfsRealtimeAdapter, GtfsRegistry
from app.adapters.open_meteo_geocoding import OpenMeteoGeocodingAdapter
from app.adapters.open_meteo_weather import OpenMeteoWeatherAdapter
from app.adapters.openrouteservice import OpenRouteServiceAdapter
from app.adapters.usgs import UsgsAdapter
from app.api.internal import build_router
from app.cache.provider_cache import ProviderCache
from app.repositories.provider_repo import ProviderRepository
from app.services.query_service import Adapters, QueryService
from app.settings import Settings, get_settings

log = get_logger("external-data")
VERSION = "1.0.0"


def build_adapters(s: Settings) -> Adapters:
    name = s.service_name
    ft, os_ = s.circuit_failure_threshold, s.circuit_open_seconds
    return Adapters(
        geocoding=OpenMeteoGeocodingAdapter(
            s.open_meteo_geocoding_url, service_name=name, failure_threshold=ft, open_seconds=os_
        ),
        weather=OpenMeteoWeatherAdapter(
            s.open_meteo_base_url,
            service_name=name,
            ttl_hourly=s.ttl_hourly_forecast,
            ttl_current=s.ttl_current_weather,
            failure_threshold=ft,
            open_seconds=os_,
        ),
        ors=OpenRouteServiceAdapter(
            s.ors_base_url,
            s.ors_api_key.get_secret_value(),
            service_name=name,
            ttl_route=s.ttl_route,
            ttl_places=s.ttl_places,
            failure_threshold=ft,
            open_seconds=os_,
        ),
        usgs=UsgsAdapter(
            s.usgs_query_url,
            service_name=name,
            ttl=s.ttl_disaster,
            min_magnitude=s.earthquake_min_magnitude,
            failure_threshold=ft,
            open_seconds=os_,
        ),
        gdacs=GdacsAdapter(
            s.gdacs_base_url, service_name=name, ttl=s.ttl_disaster, failure_threshold=ft, open_seconds=os_
        ),
        eonet=EonetAdapter(
            s.eonet_base_url, service_name=name, ttl=s.ttl_disaster, failure_threshold=ft, open_seconds=os_
        ),
        gtfs=GtfsRealtimeAdapter(
            GtfsRegistry.load(s.gtfs_provider_config),
            service_name=name,
            ttl=s.ttl_gtfs_rt,
            cache_dir=s.gtfs_cache_dir,
            failure_threshold=ft,
            open_seconds=os_,
        ),
        amadeus=AmadeusAdapter(
            s.amadeus_base_url,
            s.amadeus_client_id.get_secret_value(),
            s.amadeus_client_secret.get_secret_value(),
            service_name=name,
            ttl=s.ttl_gtfs_rt,
            failure_threshold=ft,
            open_seconds=os_,
        ),
    )


def build(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    health = HealthRegistry(s.service_name, VERSION)
    auth = InternalAuth(s.service_auth_token.get_secret_value(), s.app_env)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        adapters = build_adapters(s)
        redis = None
        cache = None
        factory = None
        engine = None
        if s.app_env != "test":
            redis = make_redis(s.redis_url)
            cache = Cache(redis, s.app_env, s.service_name)
            dsn = build_dsn(
                host=s.postgres_host,
                port=s.postgres_port,
                db=s.postgres_db,
                user=s.db_user,
                password=s.postgres_provider_password.get_secret_value(),
            )
            engine = create_engine(dsn, schema="provider")
            factory = session_factory(engine)
            health.add("postgres", _pinger(engine), critical=True)
            health.add("redis", cache.ping, critical=False)
        repo = ProviderRepository(factory)
        try:
            await repo.upsert_providers([a.descriptor for a in adapters.all()])
        except Exception as exc:  # noqa: BLE001 - DB may lag at boot; readiness reflects it
            log.warning("provider_registry_upsert_deferred", error_type=type(exc).__name__)
        app.state.query_service = QueryService(
            s, adapters, ProviderCache(cache, s.service_name, s.contract_version), repo
        )
        log.info(
            "external_data_started",
            enabled_providers=[a.descriptor.name for a in adapters.all() if a.descriptor.enabled],
            unavailable=[a.descriptor.name for a in adapters.all() if not a.descriptor.enabled],
        )
        try:
            yield
        finally:
            await adapters.close()
            if redis is not None:
                await redis.aclose()
            if engine is not None:
                await engine.dispose()

    app = create_app(s, title="Smart Travel — External Data", version=VERSION, health=health, lifespan=lifespan)
    app.include_router(build_router(s, auth))
    return app


def _pinger(engine: AsyncEngine) -> Callable[[], Awaitable[None]]:
    async def check() -> None:
        await ping(engine)

    return check


app = build()
