"""Open-Meteo Geocoding adapter — https://open-meteo.com/en/docs/geocoding-api"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sta_common.http import ResilientClient
from sta_contracts.enums import ProviderKind
from sta_contracts.geo import Point
from sta_contracts.models import LocationRef

from app.adapters.base import BaseAdapter, ProviderDescriptor, ProviderError, ProviderErrorCode


class _OMPlace(BaseModel):
    """Typed provider response. extra=allow so new optional provider fields do not break; required ones are strict."""

    model_config = ConfigDict(extra="allow")
    id: int
    name: str
    latitude: float
    longitude: float
    country_code: str | None = None
    country: str | None = None
    admin1: str | None = None
    timezone: str | None = None
    feature_code: str | None = None
    population: int | None = None


class _OMGeocodeResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    results: list[_OMPlace] = Field(default_factory=list)


class OpenMeteoGeocodingAdapter(BaseAdapter):
    descriptor = ProviderDescriptor(
        name="open_meteo_geocoding",
        kind=ProviderKind.GEOCODING,
        version="v1",
        authority="LICENSED_PROVIDER",
        license="CC BY 4.0 (non-commercial use)",
        attribution="Geocoding by Open-Meteo.com (GeoNames data)",
        docs_url="https://open-meteo.com/en/docs/geocoding-api",
        coverage_note="Populated places worldwide (GeoNames); no street-level addresses",
    )

    def __init__(self, base_url: str, *, service_name: str, **kw: Any) -> None:
        super().__init__(service_name=service_name, **kw)
        self.client = ResilientClient(
            service_name=service_name,
            dependency=self.descriptor.name,
            base_url=base_url,
            read_timeout=6,
            total_timeout=8,
        )

    async def search(
        self, query: str, *, locale: str = "en", country: str | None = None, count: int = 8
    ) -> list[LocationRef]:
        q = query.strip()
        if len(q) < 2:
            return []
        params: dict[str, Any] = {
            "name": q[:100],
            "count": max(1, min(count, 20)),
            "language": locale.split("-")[0],
            "format": "json",
        }
        if country:
            params["countryCode"] = country.upper()
        resp = await self._call(self.client, "GET", "/v1/search", params=params)
        parsed = _OMGeocodeResponse.model_validate(self._json(resp, self.descriptor.name))
        out: list[LocationRef] = []
        for p in parsed.results:
            if not p.country_code or not p.timezone:
                # We never guess a coordinate/timezone the provider did not give.
                continue
            label = ", ".join(x for x in (p.name, p.admin1, p.country) if x)
            out.append(
                LocationRef(
                    place_id=str(p.id),
                    display_name=label[:256],
                    coordinates=Point(coordinates=[p.longitude, p.latitude]),
                    country_code=p.country_code.upper(),
                    admin1=p.admin1,
                    timezone=p.timezone,
                    provider=self.descriptor.name,
                    confirmed_by_user=False,
                )
            )
        return out

    async def close(self) -> None:
        await self.client.aclose()


__all__ = ["OpenMeteoGeocodingAdapter", "ProviderError", "ProviderErrorCode"]
