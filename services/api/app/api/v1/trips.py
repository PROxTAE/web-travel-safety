"""Trips CRUD with optimistic concurrency (ETag = revision, PATCH requires If-Match), assessments, apply-route."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from sta_common.envelope import ok
from sta_common.errors import AppError, ErrorCode
from sta_contracts.models import Trip

from app.api.schemas import ApplyRoute, AssessmentCreate, TripCreate, TripPatch
from app.application.assessments import StartParams
from app.auth.deps import CurrentUser, user_scope_hash
from app.domain import rules
from app.middleware.rate_limit import user_limit
from app.settings import Settings


def etag(trip: Trip) -> str:
    return f'W/"{trip.revision}"'


def _parse_if_match(value: str | None) -> int:
    if not value:
        raise AppError(ErrorCode.PRECONDITION_FAILED, "If-Match header with the current revision is required")
    raw = value.strip().removeprefix("W/").strip('"')
    if not raw.isdigit():
        raise AppError(ErrorCode.PRECONDITION_FAILED, "If-Match must be the trip revision")
    return int(raw)


def build_router(settings: Settings) -> APIRouter:
    r = APIRouter(prefix="/api/v1", tags=["trips"])
    cv = settings.contract_version
    limited = user_limit("default", "rate_limit_default_per_minute")
    assess_limited = user_limit("assessment", "rate_limit_assessment_per_minute")

    def _validate_inputs(origin: Any, destination: Any, departure: datetime, ret: datetime | None, tz: str) -> None:
        rules.valid_timezone(tz)
        rules.validate_location(origin, "origin", require_confirmed=True)
        rules.validate_location(destination, "destination", require_confirmed=True)
        rules.validate_trip_times(
            departure,
            ret,
            max_horizon_days=settings.max_trip_horizon_days,
            max_past_minutes=settings.max_past_departure_minutes,
        )

    def _respond(trip: Trip, response: Response, status: int = 200) -> dict[str, Any]:
        response.headers["ETag"] = etag(trip)
        response.status_code = status
        return ok(trip.model_dump(mode="json"), cv)

    async def _owned(request: Request, user: CurrentUser, trip_id: UUID) -> Trip:
        trip: Trip | None = await request.app.state.repo.trips.get(user.id, trip_id)
        if trip is None:
            raise AppError(ErrorCode.NOT_FOUND, "trip not found")  # never reveal other users' trips
        return trip

    @r.post("/trips", status_code=201)
    async def create_trip(
        body: TripCreate, request: Request, response: Response, user: CurrentUser = Depends(limited)
    ) -> dict[str, Any]:
        _validate_inputs(body.origin, body.destination, body.departure_time, body.return_time, body.timezone)
        repo = request.app.state.repo
        trip = await repo.trips.create(
            user.id,
            {
                "origin_json": body.origin.model_dump(mode="json"),
                "destination_json": body.destination.model_dump(mode="json"),
                "departure_time": body.departure_time,
                "return_time": body.return_time,
                "modes": [m.value for m in body.travel_modes],
                "preferences_json": body.preferences.model_dump(mode="json"),
                "timezone": body.timezone,
            },
        )
        await repo.audit.log(user.id, "trip.create", "trip", str(trip.id))
        return _respond(trip, response, 201)

    @r.get("/trips")
    async def list_trips(request: Request, user: CurrentUser = Depends(limited), limit: int = 50) -> dict[str, Any]:
        trips = await request.app.state.repo.trips.list(user.id, limit=max(1, min(limit, 100)))
        return ok([t.model_dump(mode="json") for t in trips], cv)

    @r.get("/trips/{trip_id}")
    async def get_trip(
        trip_id: UUID, request: Request, response: Response, user: CurrentUser = Depends(limited)
    ) -> dict[str, Any]:
        return _respond(await _owned(request, user, trip_id), response)

    @r.patch("/trips/{trip_id}")
    async def patch_trip(
        trip_id: UUID,
        body: TripPatch,
        request: Request,
        response: Response,
        user: CurrentUser = Depends(limited),
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> dict[str, Any]:
        expected = _parse_if_match(if_match)
        trip = await _owned(request, user, trip_id)
        if trip.revision != expected:
            raise AppError(ErrorCode.PRECONDITION_FAILED, f"trip revision is {trip.revision}")
        origin = body.origin or trip.origin
        destination = body.destination or trip.destination
        departure = body.departure_time or trip.departure_time
        ret = body.return_time if "return_time" in body.model_fields_set else trip.return_time
        tz = body.timezone or trip.timezone
        _validate_inputs(origin, destination, departure, ret, tz)
        values: dict[str, Any] = {
            "origin_json": origin.model_dump(mode="json"),
            "destination_json": destination.model_dump(mode="json"),
            "departure_time": departure,
            "return_time": ret,
            "modes": [m.value for m in (body.travel_modes or trip.travel_modes)],
            "preferences_json": (body.preferences or trip.preferences).model_dump(mode="json"),
            "timezone": tz,
        }
        if body.status:
            values["status"] = body.status
        if body.risk_acknowledged:
            values["risk_acknowledged_at"] = datetime.now(UTC)
        updated = await request.app.state.repo.trips.update_revision(user.id, trip_id, expected, values)
        if updated is None:
            raise AppError(ErrorCode.PRECONDITION_FAILED, "trip was modified concurrently")
        await request.app.state.repo.audit.log(user.id, "trip.update", "trip", str(trip_id), revision=updated.revision)
        return _respond(updated, response)

    @r.delete("/trips/{trip_id}")
    async def delete_trip(trip_id: UUID, request: Request, user: CurrentUser = Depends(limited)) -> dict[str, Any]:
        await _owned(request, user, trip_id)
        repo = request.app.state.repo
        await repo.trips.soft_delete(user.id, trip_id)
        await repo.audit.log(user.id, "trip.delete", "trip", str(trip_id))
        return ok(
            {"trip_id": str(trip_id), "deletion_status": "SOFT_DELETED", "purge_after_days": settings.retention_days},
            cv,
        )

    @r.post("/trips/{trip_id}/assessments", status_code=202)
    async def create_assessment(
        trip_id: UUID,
        request: Request,
        body: AssessmentCreate | None = None,
        user: CurrentUser = Depends(assess_limited),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
    ) -> dict[str, Any]:
        trip = await _owned(request, user, trip_id)
        body = body or AssessmentCreate()
        conv = await request.app.state.repo.conversations.for_trip(user.id, trip_id)
        if conv is None:
            conv = await request.app.state.repo.conversations.create(
                user.id, trip_id, f"{trip.origin.display_name} → {trip.destination.display_name}"
            )
        result = await request.app.state.assessments.start(
            user,
            StartParams(
                trip=trip,
                idempotency_key=idempotency_key,
                question=body.question,
                intent_hint=body.intent_hint,
                conversation_id=conv.id,
                previous_recommendation_id=trip.latest_recommendation_id,
            ),
        )
        ref = result.ref
        if body.question and not result.replayed:
            await request.app.state.repo.conversations.add_message(
                user.id, conv.id, "user", body.question, request_id=ref.request_id
            )
        return ok(ref.model_dump(mode="json"), cv)

    @r.get("/trips/{trip_id}/assessments")
    async def list_assessments(trip_id: UUID, request: Request, user: CurrentUser = Depends(limited)) -> dict[str, Any]:
        await _owned(request, user, trip_id)
        rows = await request.app.state.repo.requests.for_trip(user.id, trip_id)
        return ok(
            [
                {
                    "request_id": str(x["id"]),
                    "kind": x["kind"],
                    "status": x["status"],
                    "trip_revision": x["trip_revision"],
                    "recommendation_id": str(x["recommendation_id"]) if x.get("recommendation_id") else None,
                    "created_at": x["created_at"],
                    "completed_at": x.get("completed_at"),
                }
                for x in rows
            ],
            cv,
        )

    @r.post("/trips/{trip_id}/apply-route", status_code=202)
    async def apply_route(
        trip_id: UUID,
        body: ApplyRoute,
        request: Request,
        response: Response,
        user: CurrentUser = Depends(assess_limited),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
    ) -> dict[str, Any]:
        expected = _parse_if_match(if_match)
        trip = await _owned(request, user, trip_id)
        if trip.revision != expected:
            raise AppError(ErrorCode.PRECONDITION_FAILED, f"trip revision is {trip.revision}")
        st = request.app.state
        rec = await st.recommendation.get(
            body.recommendation_id, user_scope_hash(user.id, settings.user_scope_salt.get_secret_value())
        )
        if rec is None or rec.trip_id != trip_id:
            raise AppError(ErrorCode.NOT_FOUND, "recommendation not found for this trip")
        candidates = {rt.route_id for rt in rec.alternatives} | (
            {rec.primary_route.route_id} if rec.primary_route else set()
        )
        if body.route_id not in candidates:
            raise AppError(ErrorCode.VALIDATION_ERROR, "route_id is not one of the recommended routes")
        if rec.action_code.value == "AVOID" and not body.acknowledge_risk:
            raise AppError(
                ErrorCode.POLICY_VALIDATION_FAILED, "recommendation is AVOID: explicit risk acknowledgement required"
            )
        values: dict[str, Any] = {
            "selected_route_id": body.route_id,
            "previous_route_id": trip.selected_route_id,
        }
        if body.acknowledge_risk:
            values["risk_acknowledged_at"] = datetime.now(UTC)
        updated = await st.repo.trips.update_revision(user.id, trip_id, expected, values)
        if updated is None:
            raise AppError(ErrorCode.PRECONDITION_FAILED, "trip was modified concurrently")
        await st.repo.audit.log(
            user.id, "trip.apply_route", "trip", str(trip_id), route_id=str(body.route_id), revision=updated.revision
        )
        conv = await st.repo.conversations.for_trip(user.id, trip_id)
        result = await st.assessments.start(
            user,
            StartParams(
                trip=updated,
                idempotency_key=f"apply:{idempotency_key}" if idempotency_key else None,
                kind="ASSESSMENT",
                conversation_id=conv.id if conv else None,
                previous_recommendation_id=body.recommendation_id,
            ),
        )
        response.headers["ETag"] = etag(updated)
        return ok({"trip": updated.model_dump(mode="json"), "run": result.ref.model_dump(mode="json")}, cv)

    return r
