"""FastAPI dependencies: bearer -> Principal -> internal user row (ownership authority is always ``user.id``)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Depends, Request
from sta_common.context import get_context
from sta_common.errors import AppError, ErrorCode

from app.auth.jwt import Principal


@dataclass(slots=True)
class CurrentUser:
    id: UUID
    principal: Principal
    locale: str
    timezone: str
    deleted: bool

    @property
    def roles(self) -> frozenset[str]:
        return self.principal.roles


def user_scope_hash(user_id: UUID, salt: str) -> str:
    """Pseudonymous owner handle for downstream services (recommendation/agent); never the Keycloak subject."""
    return hashlib.sha256(f"{salt}:{user_id}".encode()).hexdigest()[:40]


async def bearer_principal(request: Request) -> Principal:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AppError(ErrorCode.AUTHENTICATION_REQUIRED, "bearer token required")
    principal: Principal = await request.app.state.verifier.verify(token.strip())
    get_context().subject = principal.token_fingerprint  # fingerprint only; never logged as a token
    return principal


async def current_user(request: Request, principal: Principal = Depends(bearer_principal)) -> CurrentUser:
    repo = request.app.state.repo
    row: dict[str, Any] = await repo.users.get_or_create(principal.subject, display_name=principal.display_name)
    if row.get("deleted_at"):
        raise AppError(ErrorCode.FORBIDDEN, "account scheduled for deletion")
    return CurrentUser(id=row["id"], principal=principal, locale=row["locale"], timezone=row["timezone"], deleted=False)


def require_role(role: str) -> Any:
    async def dep(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if role not in user.roles:
            raise AppError(ErrorCode.FORBIDDEN, f"role '{role}' required")
        return user

    return dep
