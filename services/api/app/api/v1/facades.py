"""Facades: locations, recommendations, conversations, safety map, emergency, feedback, alert subscriptions.

The API validates inputs and ownership, proxies to the owning service, validates the response contract and never
reinterprets a decision.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sta_common.envelope import ok
from sta_common.errors import AppError, ErrorCode
from sta_contracts.enums import ConsentType, DeliveryChannel

from app.api.schemas import ConversationCreate, FeedbackCreate, MessageCreate, SubscriptionCreate
from app.application.assessments import StartParams
from app.auth.deps import CurrentUser, user_scope_hash
from app.domain import rules
from app.middleware.rate_limit import user_limit
from app.settings import Settings


def build_router(settings: Settings) -> APIRouter:
    r = APIRouter(prefix="/api/v1", tags=["facades"])
    cv = settings.contract_version
    limited = user_limit("default", "rate_limit_default_per_minute")
    search_limited = user_limit("search", "rate_limit_search_per_minute")
    assess_limited = user_limit("assessment", "rate_limit_assessment_per_minute")

    def scope(user: CurrentUser) -> str:
        return user_scope_hash(user.id, settings.user_scope_salt.get_secret_value())

    # ------------------------------------------------------------------ locations
    @r.get("/locations/search")
    async def locations_search(
        request: Request,
        response: Response,
        q: str = Query(min_length=2, max_length=200),
        locale: str = Query(default="en", pattern=r"^[a-z]{2}(-[A-Z]{2})?$"),
        country: str | None = Query(default=None, pattern=r"^[A-Za-z]{2}$"),
        limit: int = Query(default=8, ge=1, le=20),
        user: CurrentUser = Depends(search_limited),
    ) -> dict[str, Any]:
        results = await request.app.state.external.geocode(q.strip(), locale.split("-")[0], country, limit)
        response.headers["Cache-Control"] = "private, max-age=300"
        return ok([x.model_dump(mode="json") for x in results], cv)

    # ------------------------------------------------------------------ recommendations
    @r.get("/recommendations/{rec_id}")
    async def get_recommendation(
        rec_id: UUID, request: Request, response: Response, user: CurrentUser = Depends(limited)
    ) -> dict[str, Any]:
        rec = await request.app.state.recommendation.get(rec_id, scope(user))
        if rec is None:
            raise AppError(ErrorCode.NOT_FOUND, "recommendation not found")
        trip = await request.app.state.repo.trips.get(user.id, rec.trip_id, include_deleted=True)
        if trip is None:
            raise AppError(ErrorCode.NOT_FOUND, "recommendation not found")  # ownership by trip as second lock
        response.headers["Cache-Control"] = "private, no-store"
        return ok(rec.model_dump(mode="json"), cv, degraded_services=rec.degraded_services)

    # ------------------------------------------------------------------ conversations
    @r.get("/conversations")
    async def list_conversations(
        request: Request, user: CurrentUser = Depends(limited), limit: int = 20
    ) -> dict[str, Any]:
        convs = await request.app.state.repo.conversations.list(user.id, limit=max(1, min(limit, 50)))
        return ok([c.model_dump(mode="json") for c in convs], cv)

    @r.post("/conversations", status_code=201)
    async def create_conversation(
        body: ConversationCreate, request: Request, user: CurrentUser = Depends(limited)
    ) -> dict[str, Any]:
        repo = request.app.state.repo
        title = body.title or "New conversation"
        if body.trip_id:
            trip = await repo.trips.get(user.id, body.trip_id)
            if trip is None:
                raise AppError(ErrorCode.NOT_FOUND, "trip not found")
            title = body.title or f"{trip.origin.display_name} → {trip.destination.display_name}"
        conv = await repo.conversations.create(user.id, body.trip_id, title)
        return ok(conv.model_dump(mode="json"), cv)

    @r.get("/conversations/{conv_id}/messages")
    async def list_messages(conv_id: UUID, request: Request, user: CurrentUser = Depends(limited)) -> dict[str, Any]:
        repo = request.app.state.repo
        if await repo.conversations.get(user.id, conv_id) is None:
            raise AppError(ErrorCode.NOT_FOUND, "conversation not found")
        msgs = await repo.conversations.messages(user.id, conv_id)
        return ok([m.model_dump(mode="json") for m in msgs], cv)

    @r.post("/conversations/{conv_id}/messages", status_code=202)
    async def post_message(
        conv_id: UUID, body: MessageCreate, request: Request, user: CurrentUser = Depends(assess_limited)
    ) -> dict[str, Any]:
        st = request.app.state
        conv = await st.repo.conversations.get(user.id, conv_id)
        if conv is None:
            raise AppError(ErrorCode.NOT_FOUND, "conversation not found")
        if conv.trip_id is None:
            raise AppError(ErrorCode.VALIDATION_ERROR, "conversation is not linked to a trip; create a trip first")
        trip = await st.repo.trips.get(user.id, conv.trip_id)
        if trip is None:
            raise AppError(ErrorCode.NOT_FOUND, "trip not found")
        result = await st.assessments.start(
            user,
            StartParams(
                trip=trip,
                idempotency_key=None,
                question=body.text,
                kind="FOLLOW_UP",
                conversation_id=conv.id,
                intent_hint=body.intent_hint,
                previous_recommendation_id=trip.latest_recommendation_id,
            ),
        )
        ref = result.ref
        msg = await st.repo.conversations.add_message(user.id, conv.id, "user", body.text, request_id=ref.request_id)
        return ok({"message": msg.model_dump(mode="json"), "run": ref.model_dump(mode="json")}, cv)

    # ------------------------------------------------------------------ safety map
    @r.get("/safety/events")
    async def safety_events(
        request: Request,
        response: Response,
        bbox: str = Query(description="min_lon,min_lat,max_lon,max_lat"),
        lookback_days: int = Query(default=7, ge=1, le=30),
        layers: str = Query(default="disasters,alerts"),
        limit: int = Query(default=200, ge=1, le=500),
        user: CurrentUser = Depends(search_limited),
    ) -> dict[str, Any]:
        box = rules.parse_bbox(bbox, max_degrees=settings.safety_bbox_max_degrees)
        wanted = {x.strip() for x in layers.split(",") if x.strip()}
        unknown = wanted - {"disasters", "alerts"}
        if unknown:
            raise AppError(ErrorCode.VALIDATION_ERROR, f"unknown layers: {sorted(unknown)}")
        data, degraded = await request.app.state.external.disasters(box, lookback_days)
        out: dict[str, Any] = {"bbox": data.get("bbox"), "layers": {}}
        if "disasters" in wanted:
            out["layers"]["disasters"] = list(data.get("events", []))[:limit]
        if "alerts" in wanted:
            out["layers"]["alerts"] = list(data.get("official_alerts", []))[:limit]
        response.headers["Cache-Control"] = "private, max-age=120"
        return ok(out, cv, degraded_services=degraded)

    # ------------------------------------------------------------------ emergency
    @r.get("/emergency/contacts")
    async def emergency_contacts(
        request: Request,
        country_code: str = Query(pattern=r"^[A-Za-z]{2}$"),
        subdivision: str | None = Query(default=None, max_length=64),
        service_type: str | None = Query(default=None, max_length=32),
        user: CurrentUser = Depends(limited),
    ) -> dict[str, Any]:
        contacts, limitations, version = await request.app.state.recommendation.emergency_contacts(
            country_code.upper(), subdivision, service_type, user.locale.split("-")[0]
        )
        return ok(
            {
                "contacts": [c.model_dump(mode="json") for c in contacts],
                "limitations": limitations,
                "directory_version": version,
            },
            cv,
        )

    @r.get("/emergency/nearby")
    async def emergency_nearby(
        request: Request,
        type: Literal["POLICE", "MEDICAL", "EMBASSY", "FIRE"] = Query(),  # noqa: A002 - public query name
        lat: float = Query(ge=-90, le=90),
        lon: float = Query(ge=-180, le=180),
        radius_m: int = Query(default=5000, ge=100, le=50_000),
        limit: int = Query(default=10, ge=1, le=50),
        user: CurrentUser = Depends(search_limited),
    ) -> dict[str, Any]:
        st = request.app.state
        consent = await st.repo.consents.active(user.id, ConsentType.LOCATION_ONCE.value)
        if consent is None:
            raise AppError(ErrorCode.FORBIDDEN, "LOCATION_ONCE consent is required to look up places near you")
        fc = await st.external.nearby(round(lon, 4), round(lat, 4), type, radius_m, user.locale.split("-")[0], limit)
        await st.repo.audit.log(user.id, "emergency.nearby", "consent", str(consent.id), type=type)
        return ok(fc, cv)

    # ------------------------------------------------------------------ feedback + subscriptions
    @r.post("/feedback", status_code=201)
    async def post_feedback(
        body: FeedbackCreate, request: Request, user: CurrentUser = Depends(limited)
    ) -> dict[str, Any]:
        st = request.app.state
        rec = await st.recommendation.get(body.recommendation_id, scope(user))
        if rec is None or await st.repo.trips.get(user.id, rec.trip_id, include_deleted=True) is None:
            raise AppError(ErrorCode.NOT_FOUND, "recommendation not found")
        fb = await st.recommendation.feedback(scope(user), body.recommendation_id, body.category.value, body.text)
        await st.repo.audit.log(user.id, "feedback.create", "feedback", str(fb.id), category=body.category.value)
        return ok(fb.model_dump(mode="json"), cv)

    @r.post("/alert-subscriptions", status_code=201)
    async def create_subscription(
        body: SubscriptionCreate, request: Request, user: CurrentUser = Depends(limited)
    ) -> dict[str, Any]:
        st = request.app.state
        if await st.repo.trips.get(user.id, body.trip_id) is None:
            raise AppError(ErrorCode.NOT_FOUND, "trip not found")
        consent = await st.repo.consents.get(user.id, body.consent_id)
        if (
            consent is None
            or consent.type != ConsentType.ALERT_NOTIFICATION
            or not consent.granted
            or consent.revoked_at
        ):
            raise AppError(ErrorCode.FORBIDDEN, "an active ALERT_NOTIFICATION consent is required")
        if body.channel == DeliveryChannel.SMS:
            raise AppError(ErrorCode.UNSUPPORTED_COVERAGE, "SMS delivery is not available")
        sub = await st.recommendation.subscribe(
            {
                "user_scope": scope(user),
                "trip_id": str(body.trip_id),
                "channel": body.channel.value,
                "consent_id": str(body.consent_id),
                "severity_threshold": body.severity_threshold.value,
                "locale": user.locale,
                "push_subscription": body.push_subscription,
                "email": body.email,
            }
        )
        await st.repo.audit.log(user.id, "subscription.create", "subscription", str(sub.id), channel=body.channel.value)
        return ok(sub.model_dump(mode="json"), cv)

    @r.delete("/alert-subscriptions/{sub_id}", status_code=204)
    async def delete_subscription(sub_id: UUID, request: Request, user: CurrentUser = Depends(limited)) -> Response:
        st = request.app.state
        if not await st.recommendation.unsubscribe(sub_id, scope(user)):
            raise AppError(ErrorCode.NOT_FOUND, "subscription not found")
        await st.repo.audit.log(user.id, "subscription.delete", "subscription", str(sub_id))
        return Response(status_code=204)

    return r
