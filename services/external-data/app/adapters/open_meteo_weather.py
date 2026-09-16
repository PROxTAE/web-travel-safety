"""Open-Meteo Forecast adapter — https://open-meteo.com/en/docs

Queries hourly forecasts for a batch of route sample points and aligns each sample to its ETA hour.
Missing provider values stay ``null`` with a quality flag; ``0`` is a real value.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict
from sta_common.http import ResilientClient
from sta_contracts.enums import ProviderKind, QualityFlag, SourceAuthority
from sta_contracts.geo import Point
from sta_contracts.models import WeatherForecastPoint

from app.adapters.base import BaseAdapter, ProviderDescriptor, ProviderError, ProviderErrorCode
from app.domain.canonical import (
    deterministic_id,
    escalate_weather_severity,
    provenance,
    quality,
    wmo_category,
)

HOURLY_FIELDS = [
    "temperature_2m",
    "apparent_temperature",
    "precipitation",
    "precipitation_probability",
    "snowfall",
    "weather_code",
    "visibility",
    "wind_speed_10m",
    "wind_gusts_10m",
]
CURRENT_FIELDS = [
    "temperature_2m",
    "apparent_temperature",
    "precipitation",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
]
MAX_FORECAST_DAYS = 16
BATCH_SIZE = 10  # keep URLs short and respect provider guidance for multi-location calls


class _Hourly(BaseModel):
    model_config = ConfigDict(extra="allow")
    time: list[str]
    temperature_2m: list[float | None] | None = None
    apparent_temperature: list[float | None] | None = None
    precipitation: list[float | None] | None = None
    precipitation_probability: list[float | None] | None = None
    snowfall: list[float | None] | None = None
    weather_code: list[int | None] | None = None
    visibility: list[float | None] | None = None
    wind_speed_10m: list[float | None] | None = None
    wind_gusts_10m: list[float | None] | None = None


class _Current(BaseModel):
    model_config = ConfigDict(extra="allow")
    time: str
    temperature_2m: float | None = None
    apparent_temperature: float | None = None
    precipitation: float | None = None
    weather_code: int | None = None
    wind_speed_10m: float | None = None
    wind_gusts_10m: float | None = None


class _Location(BaseModel):
    model_config = ConfigDict(extra="allow")
    latitude: float
    longitude: float
    utc_offset_seconds: int = 0
    hourly: _Hourly | None = None
    current: _Current | None = None


class WeatherSample(BaseModel):
    """A route sample to query: where and when the traveler is expected to be there."""

    index: int
    point: Point
    eta_at: datetime
    probe: str = "PLANNED"
    probe_delay_minutes: int | None = None


class OpenMeteoWeatherAdapter(BaseAdapter):
    descriptor = ProviderDescriptor(
        name="open_meteo",
        kind=ProviderKind.WEATHER,
        version="v1",
        authority="LICENSED_PROVIDER",
        license="CC BY 4.0 (non-commercial use)",
        attribution="Weather data by Open-Meteo.com",
        docs_url="https://open-meteo.com/en/docs",
        coverage_note="Global model forecast, hourly resolution, up to 16 days",
    )

    def __init__(self, base_url: str, *, service_name: str, ttl_hourly: int, ttl_current: int, **kw: Any) -> None:
        super().__init__(service_name=service_name, **kw)
        self.client = ResilientClient(
            service_name=service_name,
            dependency=self.descriptor.name,
            base_url=base_url,
            read_timeout=8,
            total_timeout=10,
        )
        self.ttl_hourly = ttl_hourly
        self.ttl_current = ttl_current

    async def forecast_for_samples(
        self, samples: list[WeatherSample], *, deadline: float | None = None
    ) -> list[WeatherForecastPoint]:
        if not samples:
            return []
        now = datetime.now(UTC)
        horizon = now + timedelta(days=MAX_FORECAST_DAYS)
        for s in samples:
            if s.eta_at > horizon:
                raise ProviderError(
                    ProviderErrorCode.OUTSIDE_COVERAGE,
                    f"forecast horizon is {MAX_FORECAST_DAYS} days; sample {s.index} is beyond it",
                )
        # group by unique point so PLANNED and DELAYED probes share one provider location
        groups: dict[tuple[float, float], list[WeatherSample]] = {}
        for s in samples:
            groups.setdefault((round(s.point.lon, 4), round(s.point.lat, 4)), []).append(s)
        keys = list(groups)
        out: list[WeatherForecastPoint] = []
        for i in range(0, len(keys), BATCH_SIZE):
            batch_keys = keys[i : i + BATCH_SIZE]
            out.extend(await self._query_batch({k: groups[k] for k in batch_keys}, now, deadline))
        return out

    async def _query_batch(
        self, groups: dict[tuple[float, float], list[WeatherSample]], now: datetime, deadline: float | None
    ) -> list[WeatherForecastPoint]:
        all_samples = [s for g in groups.values() for s in g]
        start = min(s.eta_at for s in all_samples).astimezone(UTC)
        end = max(s.eta_at for s in all_samples).astimezone(UTC)
        params: dict[str, Any] = {
            "latitude": ",".join(f"{lat:.4f}" for _, lat in groups),
            "longitude": ",".join(f"{lon:.4f}" for lon, _ in groups),
            "hourly": ",".join(HOURLY_FIELDS),
            "timezone": "UTC",
            "start_date": max(start, now).date().isoformat(),
            "end_date": (end + timedelta(hours=1)).date().isoformat(),
            "wind_speed_unit": "kmh",
            "precipitation_unit": "mm",
            "temperature_unit": "celsius",
        }
        resp = await self._call(self.client, "GET", "/v1/forecast", params=params, deadline=deadline)
        raw = self._json(resp, self.descriptor.name)
        locations_raw = raw if isinstance(raw, list) else [raw]
        if len(locations_raw) != len(groups):
            raise ProviderError(ProviderErrorCode.PROVIDER_SCHEMA_CHANGED, "forecast location count mismatch")
        fetched_at = datetime.now(UTC)
        out: list[WeatherForecastPoint] = []
        for group_samples, loc_raw in zip(groups.values(), locations_raw, strict=True):
            loc = _Location.model_validate(loc_raw)
            if loc.hourly is None:
                raise ProviderError(ProviderErrorCode.PROVIDER_SCHEMA_CHANGED, "hourly block missing")
            for sample in group_samples:
                point = self._point_from_hourly(loc, sample, fetched_at)
                if point is not None:
                    out.append(point)
        return out

    def _point_from_hourly(
        self, loc: _Location, sample: WeatherSample, fetched_at: datetime
    ) -> WeatherForecastPoint | None:
        assert loc.hourly is not None
        idx, aligned_time, exact = _align(loc.hourly.time, sample.eta_at)
        if idx is None:
            return None
        values = {f: _pick(getattr(loc.hourly, f, None), idx) for f in HOURLY_FIELDS}
        missing = [f for f, v in values.items() if v is None]
        code = values["weather_code"]
        category, base_sev = wmo_category(int(code) if code is not None else None)
        sev = escalate_weather_severity(
            base_sev,
            wind_gust_kmh=values["wind_gusts_10m"],
            precipitation_mm=values["precipitation"],
            visibility_m=values["visibility"],
        )
        flags = [] if exact else [QualityFlag.INFERRED]
        notes = (
            []
            if exact
            else [f"nearest hourly slot {aligned_time.isoformat()} used for ETA {sample.eta_at.isoformat()}"]
        )
        record_raw = {"time": loc.hourly.time[idx], **{k: values[k] for k in HOURLY_FIELDS}}
        src = provenance(
            provider=self.descriptor.name,
            record_id=None,
            authority=SourceAuthority.LICENSED_PROVIDER,
            source_url=self.descriptor.docs_url,
            license_=self.descriptor.license,
            attribution=self.descriptor.attribution,
            observed_at=None,  # forecasts are model output, not observations
            published_at=None,
            fetched_at=fetched_at,
            ttl_seconds=self.ttl_hourly,
            raw={"lat": loc.latitude, "lon": loc.longitude, **record_raw},
        )
        return WeatherForecastPoint(
            id=deterministic_id(
                self.descriptor.name,
                f"{loc.latitude:.4f},{loc.longitude:.4f}",
                loc.hourly.time[idx],
                str(sample.index),
                sample.probe,
            ),
            location=Point(coordinates=[loc.longitude, loc.latitude]),
            valid_at=aligned_time,
            route_sample_index=sample.index,
            eta_at=sample.eta_at,
            temperature_c=values["temperature_2m"],
            apparent_temperature_c=values["apparent_temperature"],
            precipitation_mm=values["precipitation"],
            precipitation_probability=values["precipitation_probability"],
            snowfall_cm=values["snowfall"],
            wind_speed_kmh=values["wind_speed_10m"],
            wind_gust_kmh=values["wind_gusts_10m"],
            visibility_m=values["visibility"],
            weather_code=int(code) if code is not None else None,
            weather_category=category,
            severity=sev,
            is_current=False,
            probe="DELAYED" if sample.probe == "DELAYED" else "PLANNED",
            probe_delay_minutes=sample.probe_delay_minutes,
            quality=quality(
                observed_at=aligned_time,
                fetched_at=fetched_at,
                ttl_seconds=self.ttl_hourly,
                missing_fields=missing,
                total_fields=len(HOURLY_FIELDS),
                extra_flags=flags,
                notes=["forecast valid_at used as observed_at"] + notes,
            ),
            source=src,
        )

    async def current(self, point: Point, *, deadline: float | None = None) -> WeatherForecastPoint:
        params: dict[str, Any] = {
            "latitude": f"{point.lat:.4f}",
            "longitude": f"{point.lon:.4f}",
            "current": ",".join(CURRENT_FIELDS),
            "timezone": "UTC",
            "wind_speed_unit": "kmh",
        }
        resp = await self._call(self.client, "GET", "/v1/forecast", params=params, deadline=deadline)
        loc = _Location.model_validate(self._json(resp, self.descriptor.name))
        if loc.current is None:
            raise ProviderError(ProviderErrorCode.PROVIDER_SCHEMA_CHANGED, "current block missing")
        fetched_at = datetime.now(UTC)
        observed = _parse_time(loc.current.time)
        c = loc.current
        category, base_sev = wmo_category(c.weather_code)
        sev = escalate_weather_severity(
            base_sev, wind_gust_kmh=c.wind_gusts_10m, precipitation_mm=c.precipitation, visibility_m=None
        )
        raw = c.model_dump()
        missing = [f for f in CURRENT_FIELDS if getattr(c, f) is None]
        return WeatherForecastPoint(
            id=deterministic_id(self.descriptor.name, "current", f"{loc.latitude:.4f},{loc.longitude:.4f}", c.time),
            location=Point(coordinates=[loc.longitude, loc.latitude]),
            valid_at=observed,
            temperature_c=c.temperature_2m,
            apparent_temperature_c=c.apparent_temperature,
            precipitation_mm=c.precipitation,
            wind_speed_kmh=c.wind_speed_10m,
            wind_gust_kmh=c.wind_gusts_10m,
            weather_code=c.weather_code,
            weather_category=category,
            severity=sev,
            is_current=True,
            quality=quality(
                observed_at=observed,
                fetched_at=fetched_at,
                ttl_seconds=self.ttl_current,
                missing_fields=missing + ["visibility", "precipitation_probability", "snowfall"],
                total_fields=len(HOURLY_FIELDS),
            ),
            source=provenance(
                provider=self.descriptor.name,
                record_id=None,
                authority=SourceAuthority.LICENSED_PROVIDER,
                source_url=self.descriptor.docs_url,
                license_=self.descriptor.license,
                attribution=self.descriptor.attribution,
                observed_at=observed,
                published_at=None,
                fetched_at=fetched_at,
                ttl_seconds=self.ttl_current,
                raw={"lat": loc.latitude, "lon": loc.longitude, **raw},
            ),
        )

    async def close(self) -> None:
        await self.client.aclose()


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def _align(times: list[str], eta: datetime) -> tuple[int | None, datetime, bool]:
    """Return index of the hourly slot matching the ETA (floor to hour); flag if not exact."""
    target = eta.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    parsed = [_parse_time(t) for t in times]
    for i, t in enumerate(parsed):
        if t == target:
            return i, t, True
    if not parsed:
        return None, target, False
    nearest = min(range(len(parsed)), key=lambda i: abs((parsed[i] - target).total_seconds()))
    if abs((parsed[nearest] - target).total_seconds()) > 3 * 3600:
        return None, target, False
    return nearest, parsed[nearest], False


def _pick(series: list[Any] | None, idx: int) -> Any:
    if series is None or idx >= len(series):
        return None
    return series[idx]


__all__ = ["OpenMeteoWeatherAdapter", "WeatherSample"]
