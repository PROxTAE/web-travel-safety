"""Internal API — 00_API_AND_DATA_CONTRACTS §5.2"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sta_common.envelope import ok
from sta_common.errors import AppError, ErrorCode
from sta_contracts.enums import TravelMode
from sta_contracts.geo import BBox, Point
from sta_contracts.models import ContextQuery, LocationRef, TravelPreference

from app.adapters.base import ProviderError
from app.adapters.open_meteo_weather import WeatherSample
from app.services.query_service import QueryService, _Outcome
from app.settings import Settings


class GeocodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)
    locale: str = "en"
    country: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    count: int = Field(default=8, ge=1, le=20)


class WeatherQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    samples: list[WeatherSample] = Field(min_length=1, max_length=40)
    include_current_at: Point | None = None


class RoutesQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: Any
    trip_id: Any
    origin: LocationRef
    destination: LocationRef
    departure_time: datetime
    travel_modes: list[TravelMode] = Field(min_length=1)
    preferences: TravelPreference = Field(default_factory=TravelPreference)
    avoid_geometries: list[Any] = Field(default_factory=list)


class TransportQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin: Point
    destination: Point
    mode: TravelMode
    carrier_code: str | None = None
    flight_number: str | None = None
    departure_date: str | None = None


class DisasterQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bbox: BBox
    lookback_days: int = Field(default=7, ge=1, le=60)


class PlacesQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location: Point
    radius_m: int = Field(default=5000, ge=100)
    type: Literal["POLICE", "MEDICAL", "EMBASSY", "FIRE"]
    locale: str = "en"
    limit: int = Field(default=10, ge=1, le=50)


PLACE_CATEGORIES = {
    "POLICE": ["police"],
    "FIRE": ["fire_station"],
    "MEDICAL": ["hospital", "clinic", "doctors"],
    "EMBASSY": ["embassy"],
}


def build_router(settings: Settings, auth_dep: Any) -> APIRouter:
    r = APIRouter(prefix="/internal/v1", dependencies=[Depends(auth_dep)], tags=["internal"])
    cv = settings.contract_version

    def svc(request: Request) -> QueryService:
        return request.app.state.query_service  # type: ignore[no-any-return]

    def _raise(exc: ProviderError) -> None:
        raise exc.to_app_error()

    @r.post("/geocode/search")
    async def geocode(body: GeocodeRequest, request: Request) -> dict[str, Any]:
        s = svc(request)
        try:
            results = await s.a.geocoding.search(body.query, locale=body.locale, country=body.country, count=body.count)
        except ProviderError as exc:
            _raise(exc)
        return ok([x.model_dump(mode="json") for x in results], cv)

    @r.post("/weather/query")
    async def weather(body: WeatherQuery, request: Request) -> dict[str, Any]:
        s = svc(request)
        try:
            records = await s.a.weather.forecast_for_samples(body.samples)
            if body.include_current_at:
                records.append(await s.a.weather.current(body.include_current_at))
        except ProviderError as exc:
            _raise(exc)
        return ok([x.model_dump(mode="json") for x in records], cv)

    @r.post("/routes/query")
    async def routes(body: RoutesQuery, request: Request) -> dict[str, Any]:
        s = svc(request)
        q = ContextQuery(
            request_id=body.request_id,
            trip_id=body.trip_id,
            origin=body.origin,
            destination=body.destination,
            departure_time=body.departure_time,
            travel_modes=body.travel_modes,
            preferences=body.preferences,
            avoid_geometries=body.avoid_geometries,
            include=["routes"],
        )
        outcome = _Outcome()
        candidates = await s.routes(q, outcome, settings.context_deadline_seconds)
        return ok(
            {
                "routes": [c.model_dump(mode="json") for c in candidates],
                "unavailable_capabilities": outcome.unavailable,
            },
            cv,
            degraded_services=outcome.degraded,
        )

    @r.post("/transport/query")
    async def transport(body: TransportQuery, request: Request) -> dict[str, Any]:
        s = svc(request)
        try:
            if body.mode == TravelMode.FLIGHT:
                if not (body.carrier_code and body.flight_number and body.departure_date):
                    raise AppError(
                        ErrorCode.VALIDATION_ERROR, "flight status needs carrier_code, flight_number and departure_date"
                    )
                records = await s.a.amadeus.flight_status(body.carrier_code, body.flight_number, body.departure_date)
            else:
                records = await s.a.gtfs.status(body.origin, body.destination, body.mode)
        except ProviderError as exc:
            _raise(exc)
        return ok([x.model_dump(mode="json") for x in records], cv)

    @r.post("/disasters/query")
    async def disasters(body: DisasterQuery, request: Request) -> dict[str, Any]:
        s = svc(request)
        outcome = _Outcome()
        q = ContextQuery(
            request_id="00000000-0000-0000-0000-000000000000",
            trip_id="00000000-0000-0000-0000-000000000000",
            origin=_loc(body.bbox.min_lon, body.bbox.min_lat),
            destination=_loc(body.bbox.max_lon, body.bbox.max_lat),
            departure_time=datetime.now().astimezone(),
            travel_modes=[TravelMode.CAR],
            include=["disasters"],
        )
        events, bbox = await s.disasters(q, [], outcome, settings.context_deadline_seconds)
        return ok(
            {
                "events": [e.model_dump(mode="json") for e in events],
                "official_alerts": [e.model_dump(mode="json") for e in events if e.official],
                "bbox": bbox.as_list(),
            },
            cv,
            degraded_services=outcome.degraded,
        )

    @r.post("/places/nearby")
    async def places(body: PlacesQuery, request: Request) -> dict[str, Any]:
        s = svc(request)
        radius = min(body.radius_m, settings.places_max_radius_m)
        try:
            fc = await s.a.ors.nearby(
                body.location,
                radius_m=radius,
                category_names=PLACE_CATEGORIES[body.type],
                limit=min(body.limit, settings.places_max_results),
            )
        except ProviderError as exc:
            _raise(exc)
        return ok(fc, cv)

    @r.post("/context/query")
    async def context(body: ContextQuery, request: Request) -> dict[str, Any]:
        s = svc(request)
        ctx = await s.context(body)
        return ok(ctx.model_dump(mode="json"), cv, degraded_services=ctx.degraded_services)

    @r.get("/providers/health")
    async def providers_health(request: Request) -> dict[str, Any]:
        s = svc(request)
        return ok([h.model_dump(mode="json") for h in s.health()], cv)

    return r


def _loc(lon: float, lat: float) -> LocationRef:
    return LocationRef(
        place_id="bbox",
        display_name="bbox",
        coordinates=Point(coordinates=[lon, lat]),
        country_code="ZZ",
        timezone="UTC",
        provider="query",
    )
