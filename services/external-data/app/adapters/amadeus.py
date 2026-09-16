"""Amadeus Self-Service (production) adapter — flight schedule/status.

Acceptance rule (04 plan): only production credentials count. Without them the capability is
UNAVAILABLE and no sample flights are ever returned. Token is cached until shortly before expiry
and refreshed under a lock to prevent stampedes.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sta_common.http import ResilientClient
from sta_contracts.enums import ProviderKind, SourceAuthority, TransportServiceStatus, TravelMode
from sta_contracts.models import TransportStatus

from app.adapters.base import BaseAdapter, ProviderDescriptor, ProviderError, ProviderErrorCode
from app.domain.canonical import deterministic_id, provenance, quality


class _Token(BaseModel):
    model_config = ConfigDict(extra="allow")
    access_token: str
    expires_in: int


class _FlightPoint(BaseModel):
    model_config = ConfigDict(extra="allow")
    iataCode: str  # noqa: N815
    departure: dict[str, Any] | None = None
    arrival: dict[str, Any] | None = None


class _DatedFlight(BaseModel):
    model_config = ConfigDict(extra="allow")
    scheduledDepartureDate: str  # noqa: N815
    flightDesignator: dict[str, Any]  # noqa: N815
    flightPoints: list[_FlightPoint] = Field(default_factory=list)  # noqa: N815
    segments: list[dict[str, Any]] = Field(default_factory=list)


class _ScheduleResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    data: list[_DatedFlight] = Field(default_factory=list)


def _timing(point: dict[str, Any] | None, key: str = "timings") -> datetime | None:
    if not point:
        return None
    for t in point.get(key) or []:
        if t.get("value"):
            try:
                return datetime.fromisoformat(t["value"]).astimezone(UTC)
            except ValueError:
                return None
    return None


class AmadeusAdapter(BaseAdapter):
    descriptor = ProviderDescriptor(
        name="amadeus",
        kind=ProviderKind.FLIGHT,
        version="self-service-v2",
        authority="LICENSED_PROVIDER",
        license="Amadeus for Developers terms (production)",
        attribution="Flight data provided by Amadeus",
        docs_url="https://developers.amadeus.com/self-service/category/flights",
        coverage_note="Flight schedule status by carrier + flight number + date (production credentials only)",
    )

    def __init__(
        self, base_url: str, client_id: str, client_secret: str, *, service_name: str, ttl: int, **kw: Any
    ) -> None:
        super().__init__(service_name=service_name, **kw)
        self.enabled = bool(client_id and client_secret) and "test.api" not in base_url
        self.descriptor.enabled = self.enabled
        if not self.enabled:
            self.descriptor.coverage_note = "UNAVAILABLE: Amadeus production credentials not configured"
        self.client_id = client_id
        self.client_secret = client_secret
        self.client = ResilientClient(
            service_name=service_name, dependency="amadeus", base_url=base_url, read_timeout=10, total_timeout=12
        )
        self.ttl = ttl
        self._token: str | None = None
        self._token_expiry = 0.0
        self._lock = asyncio.Lock()

    async def _bearer(self, deadline: float | None) -> str:
        if self._token and time.monotonic() < self._token_expiry - 60:
            return self._token
        async with self._lock:
            if self._token and time.monotonic() < self._token_expiry - 60:
                return self._token
            resp = await self._call(
                self.client,
                "POST",
                "/v1/security/oauth2/token",
                content=(
                    f"grant_type=client_credentials&client_id={self.client_id}&client_secret={self.client_secret}"
                ).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                deadline=deadline,
                idempotent=True,
            )
            tok = _Token.model_validate(self._json(resp, "amadeus"))
            self._token = tok.access_token
            self._token_expiry = time.monotonic() + tok.expires_in
            return self._token

    async def flight_status(
        self, carrier_code: str, flight_number: str, departure_date: str, *, deadline: float | None = None
    ) -> list[TransportStatus]:
        if not self.enabled:
            raise ProviderError(ProviderErrorCode.NOT_CONFIGURED, "amadeus: production credentials not configured")
        token = await self._bearer(deadline)
        resp = await self._call(
            self.client,
            "GET",
            "/v2/schedule/flights",
            params={
                "carrierCode": carrier_code.upper(),
                "flightNumber": flight_number,
                "scheduledDepartureDate": departure_date,
            },
            headers={"Authorization": f"Bearer {token}"},
            deadline=deadline,
        )
        parsed = _ScheduleResponse.model_validate(self._json(resp, "amadeus"))
        fetched_at = datetime.now(UTC)
        out: list[TransportStatus] = []
        for df in parsed.data:
            points = df.flightPoints
            dep = points[0] if points else None
            arr = points[-1] if len(points) > 1 else None
            sched_dep = _timing(dep.departure if dep else None)
            sched_arr = _timing(arr.arrival if arr else None)
            raw = df.model_dump()
            # Amadeus schedule endpoint gives schedule; absence of a status field is UNKNOWN, not ON_TIME.
            out.append(
                TransportStatus(
                    id=deterministic_id("amadeus", carrier_code, flight_number, departure_date),
                    mode=TravelMode.FLIGHT,
                    operator=carrier_code.upper(),
                    service_number=f"{carrier_code.upper()}{flight_number}",
                    origin_stop=dep.iataCode if dep else None,
                    destination_stop=arr.iataCode if arr else None,
                    scheduled_departure=sched_dep,
                    scheduled_arrival=sched_arr,
                    status=TransportServiceStatus.UNKNOWN,
                    delay_minutes=None,
                    cancellation=False,
                    message="Scheduled flight found; live operational status not provided by this endpoint",
                    quality=quality(observed_at=fetched_at, fetched_at=fetched_at, ttl_seconds=self.ttl, coverage=0.5),
                    source=provenance(
                        provider="amadeus",
                        record_id=f"{carrier_code.upper()}{flight_number}:{departure_date}",
                        authority=SourceAuthority.LICENSED_PROVIDER,
                        source_url=self.descriptor.docs_url,
                        license_=self.descriptor.license,
                        attribution=self.descriptor.attribution,
                        observed_at=fetched_at,
                        published_at=None,
                        fetched_at=fetched_at,
                        ttl_seconds=self.ttl,
                        raw=raw,
                    ),
                )
            )
        return out

    async def close(self) -> None:
        await self.client.aclose()
