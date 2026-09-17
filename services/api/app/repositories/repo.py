"""Repositories over the Store. Ownership is a query predicate everywhere (``user_id=owner``)."""

from __future__ import annotations

import builtins
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sta_contracts.models import ConsentRecord, Conversation, ConversationMessage, Trip, UserProfile

from app.repositories import models as m
from app.repositories.store import Row, Store


def now() -> datetime:
    return datetime.now(UTC)


class Users:
    def __init__(self, store: Store) -> None:
        self.s = store

    async def get_or_create(self, subject: str, *, display_name: str | None) -> Row:
        rows = await self.s.find(m.user_profiles, subject_id=subject, limit=1)
        if rows:
            return rows[0]
        ts = now()
        return await self.s.insert(
            m.user_profiles,
            {
                "id": uuid4(),
                "subject_id": subject,
                "locale": "en-US",
                "timezone": "UTC",
                "display_name": display_name,
                "created_at": ts,
                "updated_at": ts,
                "deleted_at": None,
            },
        )

    async def get(self, user_id: UUID) -> Row | None:
        return await self.s.get(m.user_profiles, user_id)

    async def update(self, user_id: UUID, values: Row) -> Row | None:
        return await self.s.update(m.user_profiles, user_id, {**values, "updated_at": now()})

    async def profile(self, user_id: UUID) -> UserProfile:
        row = await self.s.get(m.user_profiles, user_id)
        assert row is not None
        cons = await self.s.find(m.consents, user_id=user_id)
        latest: dict[str, Row] = {}
        for c in cons:  # newest first; keep the latest record per type
            latest.setdefault(c["type"], c)
        has_profile = (await self.s.count(m.emergency_profiles, user_id=user_id)) > 0
        return UserProfile(
            id=row["id"],
            locale=row["locale"],
            timezone=row["timezone"],
            display_name=row.get("display_name"),
            consents=[_consent(c) for c in latest.values()],
            has_emergency_profile=has_profile,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def _consent(c: Row) -> ConsentRecord:
    return ConsentRecord(
        id=c["id"],
        type=c["type"],
        granted=c["granted"],
        policy_version=c["policy_version"],
        granted_at=c.get("granted_at"),
        revoked_at=c.get("revoked_at"),
        expires_at=c.get("expires_at"),
    )


class Consents:
    def __init__(self, store: Store) -> None:
        self.s = store

    async def record(
        self, user_id: UUID, ctype: str, granted: bool, policy_version: str, expires_at: datetime | None
    ) -> ConsentRecord:
        ts = now()
        # revoking = new record with granted=False; previous granted records of the type are closed
        if not granted:
            for prev in await self.s.find(m.consents, user_id=user_id, type=ctype, granted=True):
                if prev.get("revoked_at") is None:
                    await self.s.update(m.consents, prev["id"], {"revoked_at": ts})
        row = await self.s.insert(
            m.consents,
            {
                "id": uuid4(),
                "user_id": user_id,
                "type": ctype,
                "granted": granted,
                "policy_version": policy_version,
                "granted_at": ts if granted else None,
                "revoked_at": None if granted else ts,
                "expires_at": expires_at,
                "created_at": ts,
            },
        )
        return _consent(row)

    async def active(self, user_id: UUID, ctype: str) -> ConsentRecord | None:
        rows = await self.s.find(m.consents, user_id=user_id, type=ctype, granted=True)
        ts = now()
        for r in rows:
            if r.get("revoked_at") is None and (r.get("expires_at") is None or r["expires_at"] > ts):
                return _consent(r)
        return None

    async def get(self, user_id: UUID, consent_id: UUID) -> ConsentRecord | None:
        r = await self.s.get(m.consents, consent_id, user_id=user_id)
        return _consent(r) if r else None


class EmergencyProfiles:
    def __init__(self, store: Store) -> None:
        self.s = store

    async def get(self, user_id: UUID) -> Row | None:
        rows = await self.s.find(m.emergency_profiles, user_id=user_id, order_by="updated_at", limit=1)
        return rows[0] if rows else None

    async def put(self, user_id: UUID, blob: bytes, key_version: int) -> datetime:
        ts = now()
        existing = await self.get(user_id)
        if existing:
            await self.s.update(
                m.emergency_profiles,
                existing["id"],
                {"encrypted_payload": blob, "key_version": key_version, "updated_at": ts},
            )
        else:
            await self.s.insert(
                m.emergency_profiles,
                {
                    "id": uuid4(),
                    "user_id": user_id,
                    "encrypted_payload": blob,
                    "key_version": key_version,
                    "updated_at": ts,
                },
            )
        return ts

    async def delete(self, user_id: UUID) -> bool:
        existing = await self.get(user_id)
        if not existing:
            return False
        await self.s.delete(m.emergency_profiles, existing["id"])
        return True


class Audit:
    def __init__(self, store: Store) -> None:
        self.s = store

    async def log(self, user_id: UUID, action: str, resource_type: str, resource_id: str | None, **meta: Any) -> None:
        await self.s.insert(
            m.audit_log,
            {
                "id": uuid4(),
                "user_id": user_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "meta_json": meta,  # callers pass identifiers/counts only, never content
                "created_at": now(),
            },
        )

    async def for_user(self, user_id: UUID) -> list[Row]:
        return await self.s.find(m.audit_log, user_id=user_id)


class DataJobs:
    def __init__(self, store: Store) -> None:
        self.s = store

    async def create(self, user_id: UUID, kind: str) -> Row:
        return await self.s.insert(
            m.data_jobs,
            {
                "id": uuid4(),
                "user_id": user_id,
                "kind": kind,
                "status": "QUEUED",
                "requested_at": now(),
                "completed_at": None,
                "result_json": {},
            },
        )

    async def finish(self, job_id: UUID, status: str, result: dict[str, Any]) -> None:
        await self.s.update(m.data_jobs, job_id, {"status": status, "completed_at": now(), "result_json": result})

    async def get(self, user_id: UUID, job_id: UUID) -> Row | None:
        return await self.s.get(m.data_jobs, job_id, user_id=user_id)

    async def list(self, user_id: UUID) -> builtins.list[Row]:
        return await self.s.find(m.data_jobs, user_id=user_id, order_by="requested_at")


class Trips:
    def __init__(self, store: Store) -> None:
        self.s = store

    async def create(self, user_id: UUID, values: Row) -> Trip:
        ts = now()
        row = await self.s.insert(
            m.trips,
            {
                "id": uuid4(),
                "user_id": user_id,
                "revision": 1,
                "status": "ACTIVE",
                "selected_route_id": None,
                "previous_route_id": None,
                "latest_request_id": None,
                "latest_recommendation_id": None,
                "risk_acknowledged_at": None,
                "created_at": ts,
                "updated_at": ts,
                "deleted_at": None,
                **values,
            },
        )
        return to_trip(row)

    async def get(self, user_id: UUID, trip_id: UUID, *, include_deleted: bool = False) -> Trip | None:
        row = await self.s.get(m.trips, trip_id, user_id=user_id)
        if row is None or (row.get("deleted_at") and not include_deleted):
            return None
        return to_trip(row)

    async def list(self, user_id: UUID, *, limit: int = 50) -> builtins.list[Trip]:
        rows = await self.s.find(m.trips, user_id=user_id, order_by="updated_at", limit=limit)
        return [to_trip(r) for r in rows if not r.get("deleted_at")]

    async def update_revision(self, user_id: UUID, trip_id: UUID, expected_revision: int, values: Row) -> Trip | None:
        row = await self.s.update(
            m.trips,
            trip_id,
            {**values, "revision": expected_revision + 1, "updated_at": now()},
            user_id=user_id,
            revision=expected_revision,
        )
        return to_trip(row) if row else None

    async def set_fields(self, user_id: UUID, trip_id: UUID, values: Row) -> Trip | None:
        row = await self.s.update(m.trips, trip_id, {**values, "updated_at": now()}, user_id=user_id)
        return to_trip(row) if row else None

    async def soft_delete(self, user_id: UUID, trip_id: UUID) -> Trip | None:
        ts = now()
        row = await self.s.update(
            m.trips, trip_id, {"status": "DELETED", "deleted_at": ts, "updated_at": ts}, user_id=user_id
        )
        return to_trip(row) if row else None


def to_trip(row: Row) -> Trip:
    return Trip(
        id=row["id"],
        revision=row["revision"],
        origin=row["origin_json"],
        destination=row["destination_json"],
        departure_time=row["departure_time"],
        return_time=row.get("return_time"),
        travel_modes=row["modes"],
        preferences=row["preferences_json"],
        timezone=row["timezone"],
        status=row["status"],
        selected_route_id=row.get("selected_route_id"),
        previous_route_id=row.get("previous_route_id"),
        latest_request_id=row.get("latest_request_id"),
        latest_recommendation_id=row.get("latest_recommendation_id"),
        risk_acknowledged_at=row.get("risk_acknowledged_at"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row.get("deleted_at"),
    )


class Requests:
    def __init__(self, store: Store) -> None:
        self.s = store

    async def create(self, values: Row) -> Row:
        ts = now()
        return await self.s.insert(
            m.requests,
            {"id": uuid4(), "status": "CREATED", "created_at": ts, "updated_at": ts, "completed_at": None, **values},
        )

    async def get(self, user_id: UUID, request_id: UUID) -> Row | None:
        return await self.s.get(m.requests, request_id, user_id=user_id)

    async def by_idempotency(self, user_id: UUID, key_hash: str) -> Row | None:
        rows = await self.s.find(m.requests, user_id=user_id, idempotency_key_hash=key_hash, limit=1)
        return rows[0] if rows else None

    async def set_status(self, request_id: UUID, status: str, **values: Any) -> Row | None:
        ts = now()
        done = {"completed_at": ts} if status in ("COMPLETED", "PARTIAL", "FAILED", "CANCELLED") else {}
        return await self.s.update(m.requests, request_id, {"status": status, "updated_at": ts, **done, **values})

    async def for_trip(self, user_id: UUID, trip_id: UUID, *, limit: int = 20) -> list[Row]:
        return await self.s.find(m.requests, user_id=user_id, trip_id=trip_id, limit=limit)


class Conversations:
    def __init__(self, store: Store) -> None:
        self.s = store

    async def create(self, user_id: UUID, trip_id: UUID | None, title: str) -> Conversation:
        ts = now()
        row = await self.s.insert(
            m.conversations,
            {
                "id": uuid4(),
                "user_id": user_id,
                "trip_id": trip_id,
                "title": title[:160],
                "last_request_id": None,
                "message_count": 0,
                "created_at": ts,
                "updated_at": ts,
            },
        )
        return _conv(row)

    async def get(self, user_id: UUID, conv_id: UUID) -> Conversation | None:
        row = await self.s.get(m.conversations, conv_id, user_id=user_id)
        return _conv(row) if row else None

    async def list(self, user_id: UUID, *, limit: int = 20) -> builtins.list[Conversation]:
        return [
            _conv(r) for r in await self.s.find(m.conversations, user_id=user_id, order_by="updated_at", limit=limit)
        ]

    async def for_trip(self, user_id: UUID, trip_id: UUID) -> Conversation | None:
        rows = await self.s.find(m.conversations, user_id=user_id, trip_id=trip_id, order_by="updated_at", limit=1)
        return _conv(rows[0]) if rows else None

    async def add_message(
        self,
        user_id: UUID,
        conv_id: UUID,
        role: str,
        text: str | None,
        request_id: UUID | None = None,
        recommendation_id: UUID | None = None,
    ) -> ConversationMessage:
        ts = now()
        row = await self.s.insert(
            m.conversation_messages,
            {
                "id": uuid4(),
                "conversation_id": conv_id,
                "user_id": user_id,
                "role": role,
                "text": text,
                "request_id": request_id,
                "recommendation_id": recommendation_id,
                "created_at": ts,
            },
        )
        conv = await self.s.get(m.conversations, conv_id, user_id=user_id)
        if conv:
            await self.s.update(
                m.conversations,
                conv_id,
                {
                    "message_count": int(conv["message_count"]) + 1,
                    "last_request_id": request_id or conv.get("last_request_id"),
                    "updated_at": ts,
                },
            )
        return _msg(row)

    async def messages(self, user_id: UUID, conv_id: UUID, *, limit: int = 100) -> builtins.list[ConversationMessage]:
        rows = await self.s.find(
            m.conversation_messages, user_id=user_id, conversation_id=conv_id, desc=False, limit=limit
        )
        return [_msg(r) for r in rows]


def _conv(r: Row) -> Conversation:
    return Conversation(
        id=r["id"],
        trip_id=r.get("trip_id"),
        title=r["title"],
        last_request_id=r.get("last_request_id"),
        message_count=r["message_count"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


def _msg(r: Row) -> ConversationMessage:
    return ConversationMessage(
        id=r["id"],
        conversation_id=r["conversation_id"],
        role=r["role"],
        text=r.get("text"),
        request_id=r.get("request_id"),
        recommendation_id=r.get("recommendation_id"),
        created_at=r["created_at"],
    )


class Repository:
    def __init__(self, factory: async_sessionmaker[AsyncSession] | None) -> None:
        self.store = Store(factory)
        self.users = Users(self.store)
        self.consents = Consents(self.store)
        self.emergency = EmergencyProfiles(self.store)
        self.audit = Audit(self.store)
        self.jobs = DataJobs(self.store)
        self.trips = Trips(self.store)
        self.requests = Requests(self.store)
        self.conversations = Conversations(self.store)
