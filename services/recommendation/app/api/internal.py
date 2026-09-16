"""Internal API — 00_API_AND_DATA_CONTRACTS §5.6"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sta_common.envelope import ok
from sta_common.errors import AppError, ErrorCode
from sta_contracts.enums import DeliveryChannel, FeedbackCategory, Severity
from sta_contracts.models import AlertSubscription, FeedbackEvent, RecommendationCreateRequest, RecommendationResponse

from app.builders.response import BuildError, build_response
from app.domain.feedback import prepare, pseudonym
from app.settings import Settings


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_scope: str = Field(min_length=8, max_length=128, description="owner identifier from the API (never email)")
    recommendation_id: UUID
    category: FeedbackCategory
    text: str | None = Field(default=None, max_length=4000)


class SubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_scope: str = Field(min_length=8, max_length=128)
    trip_id: UUID
    channel: DeliveryChannel
    consent_id: UUID
    severity_threshold: Severity = Severity.MODERATE
    locale: str = "en-US"
    push_subscription: dict[str, Any] | None = None
    email: str | None = None


class ConsentRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent_id: UUID


class AlertEvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_recommendation_id: UUID
    previous_recommendation_id: UUID | None = None


def build_router(settings: Settings, auth_dep: Any) -> APIRouter:
    r = APIRouter(prefix="/internal/v1", dependencies=[Depends(auth_dep)], tags=["internal"])
    cv = settings.contract_version
    salt = settings.pseudonym_salt.get_secret_value()

    @r.post("/recommendations", status_code=201)
    async def create_recommendation(body: RecommendationCreateRequest, request: Request) -> dict[str, Any]:
        st = request.app.state
        country = body.travel_request.destination.country_code
        contacts, limitations = st.directory.resolve(
            country, subdivision=body.travel_request.destination.admin1, locale=body.decision.locale
        )
        try:
            rec = build_response(body, contacts=contacts, contact_limitations=limitations)
        except BuildError as exc:
            raise AppError(ErrorCode.POLICY_VALIDATION_FAILED, str(exc)) from None
        owner = pseudonym(body.travel_request.user_scope_hash, salt) if body.travel_request.user_scope_hash else None
        saved = await st.repo.save_recommendation(rec, owner)
        return ok(saved.model_dump(mode="json"), cv, degraded_services=saved.degraded_services)

    @r.get("/recommendations/{rec_id}")
    async def get_recommendation(
        rec_id: UUID, request: Request, user_scope: str | None = Query(default=None)
    ) -> dict[str, Any]:
        rec, owner = await request.app.state.repo.get_recommendation(rec_id)
        if rec is None:
            raise AppError(ErrorCode.NOT_FOUND, "recommendation not found")
        if owner and user_scope and pseudonym(user_scope, salt) != owner:
            raise AppError(ErrorCode.NOT_FOUND, "recommendation not found")
        return ok(rec.model_dump(mode="json"), cv, degraded_services=rec.degraded_services)

    @r.post("/feedback", status_code=201)
    async def create_feedback(body: FeedbackRequest, request: Request) -> dict[str, Any]:
        st = request.app.state
        rec, owner = await st.repo.get_recommendation(body.recommendation_id)
        if rec is None or (owner and pseudonym(body.user_scope, salt) != owner):
            raise AppError(ErrorCode.NOT_FOUND, "recommendation not found")
        prepared = prepare(body.category, body.text, user_scope=body.user_scope, salt=salt)
        fb = FeedbackEvent(
            id=uuid4(),
            recommendation_id=body.recommendation_id,
            category=body.category,
            text_redacted=prepared.text_redacted,
            created_at=datetime.now(UTC),
        )
        saved = await st.repo.save_feedback(fb, prepared.user_pseudonym, prepared.review_severity)
        return ok(saved.model_dump(mode="json"), cv)

    @r.get("/feedback/review-queue")
    async def review_queue(request: Request) -> dict[str, Any]:
        return ok(await request.app.state.repo.review_queue(), cv)

    @r.post("/subscriptions", status_code=201)
    async def create_subscription(body: SubscriptionRequest, request: Request) -> dict[str, Any]:
        st = request.app.state
        if body.channel == DeliveryChannel.PUSH and not body.push_subscription:
            raise AppError(ErrorCode.VALIDATION_ERROR, "push channel requires push_subscription")
        if body.channel == DeliveryChannel.EMAIL and not body.email:
            raise AppError(ErrorCode.VALIDATION_ERROR, "email channel requires email")
        if body.channel == DeliveryChannel.SMS:
            raise AppError(ErrorCode.UNSUPPORTED_COVERAGE, "SMS delivery is not configured in this deployment")
        sub = AlertSubscription(
            id=uuid4(),
            trip_id=body.trip_id,
            channel=body.channel,
            consent_id=body.consent_id,
            status="ACTIVE",
            severity_threshold=body.severity_threshold,
            locale=body.locale,
            created_at=datetime.now(UTC),
        )
        target = (
            {"push_subscription": body.push_subscription}
            if body.push_subscription
            else ({"email": body.email} if body.email else {})
        )
        saved = await st.repo.save_subscription(sub, pseudonym(body.user_scope, salt), target)
        return ok(saved.model_dump(mode="json"), cv)

    @r.delete("/subscriptions/{sub_id}", status_code=204)
    async def delete_subscription(sub_id: UUID, request: Request, user_scope: str = Query(...)) -> None:
        st = request.app.state
        sub, owner, _ = await st.repo.get_subscription(sub_id)
        if sub is None or owner != pseudonym(user_scope, salt):
            raise AppError(ErrorCode.NOT_FOUND, "subscription not found")
        await st.repo.revoke_subscription(sub_id)

    @r.post("/subscriptions/revoke-consent")
    async def revoke_consent(body: ConsentRevokeRequest, request: Request) -> dict[str, Any]:
        n = await request.app.state.repo.revoke_by_consent(body.consent_id)
        return ok({"revoked": n}, cv)

    @r.post("/alerts/evaluate")
    async def alerts_evaluate(body: AlertEvaluateRequest, request: Request) -> dict[str, Any]:
        st = request.app.state
        new, _ = await st.repo.get_recommendation(body.new_recommendation_id)
        if new is None:
            raise AppError(ErrorCode.NOT_FOUND, "new recommendation not found")
        prev: RecommendationResponse | None = None
        if body.previous_recommendation_id:
            prev, _ = await st.repo.get_recommendation(body.previous_recommendation_id)
        decisions = await st.alerts.evaluate(prev, new)
        return ok([asdict(d) | {"subscription_id": str(d.subscription_id)} for d in decisions], cv)

    @r.get("/emergency/contacts")
    async def emergency_contacts(
        request: Request,
        country_code: str = Query(..., pattern=r"^[A-Za-z]{2}$"),
        subdivision: str | None = None,
        service_type: str | None = None,
        locale: str = "en",
    ) -> dict[str, Any]:
        contacts, limitations = request.app.state.directory.resolve(
            country_code.upper(),
            subdivision=subdivision,
            service_types=[service_type] if service_type else None,
            locale=locale,
        )
        return ok(
            {
                "contacts": [c.model_dump(mode="json") for c in contacts],
                "limitations": limitations,
                "directory_version": request.app.state.directory.directory_version,
            },
            cv,
        )

    return r
