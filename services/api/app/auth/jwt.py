"""OIDC bearer verification against the Keycloak realm JWKS.

Rules (02 plan, Phase 2): verify signature, issuer, audience, exp, nbf and the required realm role; refresh the JWKS
once when an unknown ``kid`` appears; never decode unsigned tokens; never log the token.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

import jwt
from jwt import PyJWK
from sta_common.errors import AppError, ErrorCode
from sta_common.http import ResilientClient
from sta_common.logging import get_logger

log = get_logger("auth")
ALLOWED_ALGS = ("RS256", "RS384", "RS512", "ES256", "ES384", "PS256")


@dataclass(slots=True)
class Principal:
    subject: str
    roles: frozenset[str]
    scopes: frozenset[str]
    issuer: str
    expires_at: int
    email_verified: bool = False
    display_name: str | None = None
    token_fingerprint: str = field(default="", repr=False)  # sha256 prefix for audit correlation, never the token


class JWKSCache:
    def __init__(self, client: ResilientClient, url_path: str, ttl_seconds: int) -> None:
        self.client = client
        self.url_path = url_path
        self.ttl = ttl_seconds
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at = 0.0
        self._unknown_refresh_at = 0.0
        self._lock = asyncio.Lock()

    async def _refresh(self) -> None:
        resp = await self.client.request("GET", self.url_path, deadline_seconds=4.0)
        if resp.status_code != 200:
            raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "identity provider keys unavailable")
        payload = resp.json()
        keys: dict[str, PyJWK] = {}
        for raw in payload.get("keys", []):
            if raw.get("use", "sig") != "sig" or raw.get("kty") not in ("RSA", "EC"):
                continue
            try:
                keys[str(raw["kid"])] = PyJWK.from_dict(raw)
            except Exception as exc:  # noqa: BLE001 - skip malformed keys; others still usable
                log.warning("jwks_key_skipped", error_type=type(exc).__name__)
        if not keys:
            raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "identity provider published no signing keys")
        self._keys = keys
        self._fetched_at = time.monotonic()

    async def get(self, kid: str) -> PyJWK | None:
        async with self._lock:
            now = time.monotonic()
            if not self._keys or now - self._fetched_at > self.ttl:
                await self._refresh()
            elif kid not in self._keys and now - self._unknown_refresh_at > 5:
                # unknown kid after rotation: one bounded refresh, not one per request (anti-storm window 5 s)
                self._unknown_refresh_at = now
                await self._refresh()
            return self._keys.get(kid)


class TokenVerifier:
    def __init__(
        self,
        jwks: JWKSCache,
        *,
        issuers: list[str],
        audience: str,
        required_role: str,
        leeway_seconds: int,
    ) -> None:
        self.jwks = jwks
        self.issuers = issuers
        self.audience = audience
        self.required_role = required_role
        self.leeway = leeway_seconds

    async def verify(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise AppError(ErrorCode.AUTHENTICATION_REQUIRED, "malformed bearer token") from None
        alg = header.get("alg")
        kid = header.get("kid")
        if alg not in ALLOWED_ALGS or not isinstance(kid, str):
            raise AppError(ErrorCode.AUTHENTICATION_REQUIRED, "unsupported token header")
        key = await self.jwks.get(kid)
        if key is None:
            raise AppError(ErrorCode.AUTHENTICATION_REQUIRED, "unknown signing key")
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key.key,
                algorithms=[alg],
                audience=self.audience,
                issuer=self.issuers,  # PyJWT >= 2.10 accepts a sequence
                leeway=self.leeway,
                options={"require": ["exp", "iat", "sub", "iss"], "verify_aud": True, "verify_iss": True},
            )
        except jwt.ExpiredSignatureError:
            raise AppError(ErrorCode.AUTHENTICATION_REQUIRED, "token expired") from None
        except jwt.InvalidAudienceError:
            raise AppError(ErrorCode.AUTHENTICATION_REQUIRED, "token audience not accepted") from None
        except jwt.InvalidIssuerError:
            raise AppError(ErrorCode.AUTHENTICATION_REQUIRED, "token issuer not accepted") from None
        except jwt.PyJWTError:
            raise AppError(ErrorCode.AUTHENTICATION_REQUIRED, "token verification failed") from None
        roles = frozenset(str(r) for r in (claims.get("realm_access") or {}).get("roles", []))
        scopes = frozenset(str(claims.get("scope", "")).split())
        if self.required_role and self.required_role not in roles:
            raise AppError(ErrorCode.FORBIDDEN, f"role '{self.required_role}' required")
        return Principal(
            subject=str(claims["sub"]),
            roles=roles,
            scopes=scopes,
            issuer=str(claims["iss"]),
            expires_at=int(claims["exp"]),
            email_verified=bool(claims.get("email_verified", False)),
            display_name=_safe_name(claims.get("name") or claims.get("preferred_username")),
            token_fingerprint=hashlib.sha256(token.encode()).hexdigest()[:16],
        )


def _safe_name(v: Any) -> str | None:
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v[:120] if v else None
