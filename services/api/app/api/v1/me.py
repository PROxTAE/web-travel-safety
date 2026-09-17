"""/me, /me/emergency-profile, /consents, /me/data-jobs (export/delete skeleton with tracked status)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sta_common.envelope import ok
from sta_common.errors import AppError, ErrorCode
from sta_contracts.enums import ConsentType
from sta_contracts.models import EmergencyProfile

from app.api.schemas import ConsentCreate, DataJobCreate, EmergencyProfilePut, MePatch
from app.auth.deps import CurrentUser, user_scope_hash
from app.domain import rules
from app.middleware.rate_limit import user_limit
from app.repositories import models as m
from app.settings import Settings


def build_router(settings: Settings) -> APIRouter:
    r = APIRouter(prefix="/api/v1", tags=["me"])
    cv = settings.contract_version
    limited = user_limit("default", "rate_limit_default_per_minute")

    @r.get("/me")
    async def get_me(request: Request, user: CurrentUser = Depends(limited)) -> dict[str, Any]:
        profile = await request.app.state.repo.users.profile(user.id)
        return ok(profile.model_dump(mode="json"), cv)

    @r.patch("/me")
    async def patch_me(body: MePatch, request: Request, user: CurrentUser = Depends(limited)) -> dict[str, Any]:
        values: dict[str, Any] = {}
        if body.locale is not None:
            values["locale"] = rules.valid_locale(body.locale)
        if body.timezone is not None:
            values["timezone"] = rules.valid_timezone(body.timezone)
        if "display_name" in body.model_fields_set:
            values["display_name"] = body.display_name
        repo = request.app.state.repo
        if values:
            await repo.users.update(user.id, values)
            await repo.audit.log(user.id, "profile.update", "user", str(user.id), fields=sorted(values))
        return ok((await repo.users.profile(user.id)).model_dump(mode="json"), cv)

    @r.get("/me/emergency-profile")
    async def get_emergency_profile(request: Request, user: CurrentUser = Depends(limited)) -> dict[str, Any]:
        st = request.app.state
        row = await st.repo.emergency.get(user.id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "no emergency profile")
        data = st.cipher.decrypt(row["encrypted_payload"], key_version=row["key_version"], aad=str(user.id))
        profile = EmergencyProfile.model_validate({**data, "updated_at": row["updated_at"]})
        await st.repo.audit.log(user.id, "emergency_profile.read", "emergency_profile", str(row["id"]))
        return ok(profile.model_dump(mode="json"), cv)

    @r.put("/me/emergency-profile")
    async def put_emergency_profile(
        body: EmergencyProfilePut, request: Request, user: CurrentUser = Depends(limited)
    ) -> dict[str, Any]:
        st = request.app.state
        consent = await st.repo.consents.active(user.id, ConsentType.EMERGENCY_PROFILE.value)
        if consent is None:
            raise AppError(ErrorCode.FORBIDDEN, "EMERGENCY_PROFILE consent is required before storing medical data")
        blob, version = st.cipher.encrypt(body.model_dump(mode="json"), aad=str(user.id))
        ts = await st.repo.emergency.put(user.id, blob, version)
        await st.repo.audit.log(user.id, "emergency_profile.update", "emergency_profile", None, key_version=version)
        # response is minimized: presence + non-medical metadata only
        return ok(
            {
                "stored": True,
                "key_version": version,
                "updated_at": ts.isoformat(),
                "contacts": len(body.contacts),
                "has_medical_notes": body.medical_notes is not None,
            },
            cv,
        )

    @r.delete("/me/emergency-profile", status_code=200)
    async def delete_emergency_profile(request: Request, user: CurrentUser = Depends(limited)) -> dict[str, Any]:
        st = request.app.state
        deleted = await st.repo.emergency.delete(user.id)
        await st.repo.audit.log(user.id, "emergency_profile.delete", "emergency_profile", None, deleted=deleted)
        return ok({"deleted": deleted, "deletion_status": "COMPLETED" if deleted else "NOTHING_TO_DELETE"}, cv)

    @r.post("/consents", status_code=201)
    async def post_consent(
        body: ConsentCreate, request: Request, user: CurrentUser = Depends(limited)
    ) -> dict[str, Any]:
        st = request.app.state
        if body.expires_at is not None and body.expires_at <= datetime.now(UTC):
            raise AppError(ErrorCode.VALIDATION_ERROR, "expires_at must be in the future")
        rec = await st.repo.consents.record(
            user.id, body.type.value, body.granted, body.policy_version, body.expires_at
        )
        await st.repo.audit.log(
            user.id, "consent.grant" if body.granted else "consent.revoke", "consent", str(rec.id), type=body.type.value
        )
        if not body.granted and body.type == ConsentType.ALERT_NOTIFICATION:
            # revoke downstream subscriptions bound to any consent of this type
            for c in await st.repo.store.find(m.consents, user_id=user.id, type=body.type.value):
                try:
                    await st.recommendation.revoke_consent(c["id"])
                except AppError:
                    pass  # recommendation degraded: subscription revocation is retried by the retention job
        if not body.granted and body.type == ConsentType.EMERGENCY_PROFILE:
            await st.repo.emergency.delete(user.id)
        return ok(rec.model_dump(mode="json"), cv)

    @r.get("/consents")
    async def list_consents(request: Request, user: CurrentUser = Depends(limited)) -> dict[str, Any]:
        profile = await request.app.state.repo.users.profile(user.id)
        return ok([c.model_dump(mode="json") for c in profile.consents], cv)

    @r.post("/me/data-jobs", status_code=202)
    async def create_data_job(
        body: DataJobCreate, request: Request, user: CurrentUser = Depends(limited)
    ) -> dict[str, Any]:
        st = request.app.state
        job = await st.repo.jobs.create(user.id, body.kind)
        await st.repo.audit.log(user.id, f"data_job.{body.kind.lower()}", "data_job", str(job["id"]))
        if body.kind == "DELETE":
            ts = datetime.now(UTC)
            await st.repo.users.update(user.id, {"deleted_at": ts})
            for trip in await st.repo.trips.list(user.id, limit=500):
                await st.repo.trips.soft_delete(user.id, trip.id)
            await st.repo.emergency.delete(user.id)
            await st.repo.jobs.finish(job["id"], "SCHEDULED", {"purge_after_days": settings.retention_days})
        else:
            trips = await st.repo.trips.list(user.id, limit=500)
            await st.repo.jobs.finish(
                job["id"],
                "COMPLETED",
                {
                    "profile": (await st.repo.users.profile(user.id)).model_dump(mode="json"),
                    "trips": [t.model_dump(mode="json") for t in trips],
                    "user_scope": user_scope_hash(user.id, settings.user_scope_salt.get_secret_value()),
                    "note": "recommendation/feedback data is exported by the recommendation service using user_scope",
                },
            )
        job2 = await st.repo.jobs.get(user.id, job["id"])
        return ok(_job(job2 or job), cv)

    @r.get("/me/data-jobs/{job_id}")
    async def get_data_job(job_id: UUID, request: Request, user: CurrentUser = Depends(limited)) -> dict[str, Any]:
        job = await request.app.state.repo.jobs.get(user.id, job_id)
        if job is None:
            raise AppError(ErrorCode.NOT_FOUND, "job not found")
        return ok(_job(job), cv)

    return r


def _job(j: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(j["id"]),
        "kind": j["kind"],
        "status": j["status"],
        "requested_at": j["requested_at"],
        "completed_at": j.get("completed_at"),
        "result": j.get("result_json") or {},
    }
