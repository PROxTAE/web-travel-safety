"""Service-to-service authentication for /internal/v1 endpoints.

Internal endpoints never accept browser JWTs. On the docker network every
service presents the shared SERVICE_AUTH_TOKEN as a bearer token. Comparison is
constant-time. In development/test an empty configured token disables the check
so unit tests can run without secrets; staging/production require it (settings).
"""

from __future__ import annotations

import hmac

from fastapi import Depends, Request

from sta_common.errors import AppError, ErrorCode


class InternalAuth:
    def __init__(self, expected_token: str, app_env: str) -> None:
        self.expected = expected_token
        self.enforced = bool(expected_token) or app_env in ("staging", "production")

    async def __call__(self, request: Request) -> None:
        if not self.enforced:
            return
        header = request.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), self.expected.encode()):
            raise AppError(ErrorCode.AUTHENTICATION_REQUIRED, "Internal service credential required")


def internal_dependency(auth: InternalAuth):  # type: ignore[no-untyped-def]
    return Depends(auth)
