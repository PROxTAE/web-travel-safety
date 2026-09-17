"""Typed facades over internal services. Every response is validated against the shared contract before it is
exposed; base URLs come from settings only (no user-supplied URLs)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sta_common.errors import AppError, ErrorCode
from sta_common.http import ResilientClient
from sta_common.logging import get_logger
from sta_contracts.models import (
    AgentRunRequest,
    AlertSubscription,
    FeedbackEvent,
    LocationRef,
    OfficialContact,
    RecommendationResponse,
    RunProgressEvent,
    RunRef,
    RunState,
    TravelRequest,
)

log = get_logger("clients")


def _data(payload: Any, dependency: str) -> Any:
    if not isinstance(payload, dict) or "data" not in payload:
        raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, f"{dependency} returned a malformed envelope")
    return payload["data"]


def _degraded(payload: Any) -> list[str]:
    meta = payload.get("meta") if isinstance(payload, dict) else None
    d = meta.get("degraded_services") if isinstance(meta, dict) else None
    return [str(x) for x in d] if isinstance(d, list) else []


class AgentClient:
    def __init__(self, c: ResilientClient, submit_timeout: float, poll_timeout: float) -> None:
        self.c = c
        self.submit_timeout = submit_timeout
        self.poll_timeout = poll_timeout

    async def start(self, req: TravelRequest, conversation_id: UUID | None, resume_from: UUID | None) -> RunRef:
        body = AgentRunRequest(travel_request=req, conversation_id=conversation_id, resume_from_request_id=resume_from)
        payload = await self.c.post_json(
            "/internal/v1/runs",
            body.model_dump(mode="json"),
            deadline_seconds=self.submit_timeout,
            idempotent=False,
        )
        return _validate(RunRef, _data(payload, "agent"), "agent")

    async def state(self, request_id: UUID) -> RunState | None:
        resp = await self.c.request("GET", f"/internal/v1/runs/{request_id}", deadline_seconds=self.poll_timeout)
        if resp.status_code == 404:
            return None
        return _validate(RunState, _data(self.c.raise_for_envelope(resp), "agent"), "agent")

    async def events(self, request_id: UUID) -> list[RunProgressEvent]:
        payload = await self.c.get_json(f"/internal/v1/runs/{request_id}/events", deadline_seconds=self.poll_timeout)
        data = _data(payload, "agent")
        if not isinstance(data, list):
            raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "agent returned malformed events")
        return [_validate(RunProgressEvent, e, "agent") for e in data]

    async def cancel(self, request_id: UUID, reason: str) -> str:
        payload = await self.c.post_json(
            f"/internal/v1/runs/{request_id}/cancel", {"reason": reason}, deadline_seconds=self.poll_timeout
        )
        return str(_data(payload, "agent").get("status", "CANCELLED"))


class ExternalDataClient:
    def __init__(self, c: ResilientClient, timeout: float) -> None:
        self.c = c
        self.timeout = timeout

    async def geocode(self, query: str, locale: str, country: str | None, count: int) -> list[LocationRef]:
        payload = await self.c.post_json(
            "/internal/v1/geocode/search",
            {"query": query, "locale": locale, "country": country, "count": count},
            deadline_seconds=self.timeout,
            idempotent=True,
        )
        return [_validate(LocationRef, x, "external-data") for x in _data(payload, "external-data")]

    async def disasters(
        self, bbox: tuple[float, float, float, float], lookback_days: int
    ) -> tuple[dict[str, Any], list[str]]:
        min_lon, min_lat, max_lon, max_lat = bbox
        payload = await self.c.post_json(
            "/internal/v1/disasters/query",
            {
                "bbox": {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat},
                "lookback_days": lookback_days,
            },
            deadline_seconds=self.timeout,
            idempotent=True,
        )
        data = _data(payload, "external-data")
        if not isinstance(data, dict):
            raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "external-data returned malformed disaster data")
        return data, _degraded(payload)

    async def nearby(self, lon: float, lat: float, kind: str, radius_m: int, locale: str, limit: int) -> dict[str, Any]:
        payload = await self.c.post_json(
            "/internal/v1/places/nearby",
            {
                "location": {"type": "Point", "coordinates": [lon, lat]},
                "radius_m": radius_m,
                "type": kind,
                "locale": locale,
                "limit": limit,
            },
            deadline_seconds=self.timeout,
            idempotent=True,
        )
        data = _data(payload, "external-data")
        if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
            raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "external-data returned malformed places")
        return data


class RecommendationClient:
    def __init__(self, c: ResilientClient, timeout: float) -> None:
        self.c = c
        self.timeout = timeout

    async def get(self, rec_id: UUID, user_scope: str) -> RecommendationResponse | None:
        resp = await self.c.request(
            "GET",
            f"/internal/v1/recommendations/{rec_id}",
            params={"user_scope": user_scope},
            deadline_seconds=self.timeout,
        )
        if resp.status_code == 404:
            return None
        payload = self.c.raise_for_envelope(resp)
        return _validate(RecommendationResponse, _data(payload, "recommendation"), "recommendation")

    async def feedback(self, user_scope: str, rec_id: UUID, category: str, text: str | None) -> FeedbackEvent:
        payload = await self.c.post_json(
            "/internal/v1/feedback",
            {"user_scope": user_scope, "recommendation_id": str(rec_id), "category": category, "text": text},
            deadline_seconds=self.timeout,
            idempotent=False,
        )
        return _validate(FeedbackEvent, _data(payload, "recommendation"), "recommendation")

    async def subscribe(self, body: dict[str, Any]) -> AlertSubscription:
        payload = await self.c.post_json(
            "/internal/v1/subscriptions", body, deadline_seconds=self.timeout, idempotent=False
        )
        return _validate(AlertSubscription, _data(payload, "recommendation"), "recommendation")

    async def unsubscribe(self, sub_id: UUID, user_scope: str) -> bool:
        resp = await self.c.request(
            "DELETE",
            f"/internal/v1/subscriptions/{sub_id}",
            params={"user_scope": user_scope},
            deadline_seconds=self.timeout,
        )
        if resp.status_code == 404:
            return False
        self.c.raise_for_envelope(resp)
        return True

    async def revoke_consent(self, consent_id: UUID) -> int:
        payload = await self.c.post_json(
            "/internal/v1/subscriptions/revoke-consent", {"consent_id": str(consent_id)}, deadline_seconds=self.timeout
        )
        return int(_data(payload, "recommendation").get("revoked", 0))

    async def alerts_evaluate(self, new_id: UUID, previous_id: UUID | None) -> list[dict[str, Any]]:
        payload = await self.c.post_json(
            "/internal/v1/alerts/evaluate",
            {
                "new_recommendation_id": str(new_id),
                "previous_recommendation_id": str(previous_id) if previous_id else None,
            },
            deadline_seconds=self.timeout,
        )
        data = _data(payload, "recommendation")
        return list(data) if isinstance(data, list) else []

    async def emergency_contacts(
        self, country_code: str, subdivision: str | None, service_type: str | None, locale: str
    ) -> tuple[list[OfficialContact], list[str], str]:
        params: dict[str, str] = {"country_code": country_code, "locale": locale}
        if subdivision:
            params["subdivision"] = subdivision
        if service_type:
            params["service_type"] = service_type
        payload = await self.c.get_json("/internal/v1/emergency/contacts", params=params, deadline_seconds=self.timeout)
        data = _data(payload, "recommendation")
        contacts = [_validate(OfficialContact, c, "recommendation") for c in data.get("contacts", [])]
        return contacts, [str(x) for x in data.get("limitations", [])], str(data.get("directory_version", ""))


def _validate[T](model: type[T], raw: Any, dependency: str) -> T:
    try:
        return model.model_validate(raw)  # type: ignore[attr-defined, no-any-return]
    except ValidationError as exc:
        log.warning("downstream_contract_violation", dependency=dependency, errors=exc.error_count())
        raise AppError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            f"{dependency} response violates the contract",
            status_code=502,
            details={"reason": "contract_violation"},
        ) from None
