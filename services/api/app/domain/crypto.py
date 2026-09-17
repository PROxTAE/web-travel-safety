"""Envelope encryption for the emergency profile (AES-256-GCM, versioned key from the environment, never the DB)."""

from __future__ import annotations

import base64
import os

import orjson
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sta_common.errors import AppError, ErrorCode


class ProfileCipher:
    def __init__(self, key_b64: str, key_version: int) -> None:
        self.enabled = bool(key_b64)
        self.key_version = key_version
        self._aead: AESGCM | None = None
        if key_b64:
            raw = base64.b64decode(key_b64)
            if len(raw) != 32:
                raise ValueError("EMERGENCY_PROFILE_ENCRYPTION_KEY must decode to 32 bytes")
            self._aead = AESGCM(raw)

    def encrypt(self, payload: dict[str, object], *, aad: str) -> tuple[bytes, int]:
        if self._aead is None:
            raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "emergency profile encryption is not configured")
        nonce = os.urandom(12)
        ct = self._aead.encrypt(nonce, orjson.dumps(payload), aad.encode())
        return nonce + ct, self.key_version

    def decrypt(self, blob: bytes, *, key_version: int, aad: str) -> dict[str, object]:
        if self._aead is None:
            raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "emergency profile encryption is not configured")
        if key_version != self.key_version:
            raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "emergency profile key version not available")
        try:
            data = self._aead.decrypt(blob[:12], blob[12:], aad.encode())
        except InvalidTag:
            raise AppError(ErrorCode.INTERNAL_ERROR, "emergency profile integrity check failed") from None
        out: dict[str, object] = orjson.loads(data)
        return out
