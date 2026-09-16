"""Delivery channels. In-app (Redis pub/sub → API SSE) is the baseline; Web Push / email only when configured.
Payloads are minimal: no coordinates, no medical data, no full explanation on the lock screen."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from redis.asyncio import Redis
from sta_common.logging import get_logger

log = get_logger("notifications")


@dataclass(slots=True)
class DeliveryResult:
    status: str  # DELIVERED | FAILED | SKIPPED
    provider_message_id: str | None = None
    error_code: str | None = None


@dataclass(slots=True)
class AlertPayload:
    subscription_id: str
    trip_id: str
    recommendation_id: str
    action_code: str
    risk_level: str
    severity: str
    title_key: str
    reasons: list[str]
    locale: str

    def minimal(self) -> dict[str, Any]:
        return {
            "type": "alert",
            "subscription_id": self.subscription_id,
            "trip_id": self.trip_id,
            "recommendation_id": self.recommendation_id,
            "action_code": self.action_code,
            "risk_level": self.risk_level,
            "severity": self.severity,
            "title_key": self.title_key,
            "reasons": self.reasons[:3],
        }


class Channel(Protocol):
    name: str

    async def deliver(self, target: dict[str, Any], payload: AlertPayload) -> DeliveryResult: ...


class InAppChannel:
    name = "IN_APP"

    def __init__(self, redis: Redis | None, env: str) -> None:
        self.redis = redis
        self.env = env
        self.sent: list[dict[str, Any]] = []  # test-mode capture

    async def deliver(self, target: dict[str, Any], payload: AlertPayload) -> DeliveryResult:
        channel = f"sta:{self.env}:user:{target['user_pseudonym']}:alerts"
        body = json.dumps(payload.minimal())
        if self.redis is None:
            self.sent.append({"channel": channel, "body": payload.minimal()})
            return DeliveryResult("DELIVERED", provider_message_id="memory")
        try:
            await self.redis.publish(channel, body)
            # also keep a short-lived inbox so a reconnecting SSE client can catch up
            inbox = f"sta:{self.env}:inbox:{target['user_pseudonym']}"
            pipe = self.redis.pipeline()
            pipe.lpush(inbox, body)
            pipe.ltrim(inbox, 0, 49)
            pipe.expire(inbox, 7 * 24 * 3600)
            await pipe.execute()
            return DeliveryResult("DELIVERED", provider_message_id=None)
        except Exception as exc:  # noqa: BLE001
            log.warning("in_app_delivery_failed", error_type=type(exc).__name__)
            return DeliveryResult("FAILED", error_code=type(exc).__name__)


class WebPushChannel:
    name = "PUSH"

    def __init__(self, vapid_private_key: str, vapid_subject: str) -> None:
        self.enabled = bool(vapid_private_key and vapid_subject)
        self.key = vapid_private_key
        self.subject = vapid_subject

    async def deliver(self, target: dict[str, Any], payload: AlertPayload) -> DeliveryResult:
        if not self.enabled:
            return DeliveryResult("SKIPPED", error_code="PUSH_NOT_CONFIGURED")
        sub = target.get("push_subscription")
        if not sub:
            return DeliveryResult("FAILED", error_code="NO_PUSH_SUBSCRIPTION")
        try:
            from pywebpush import webpush

            resp = webpush(
                subscription_info=sub,
                data=json.dumps(payload.minimal()),
                vapid_private_key=self.key,
                vapid_claims={"sub": self.subject},
                ttl=3600,
            )
            return DeliveryResult(
                "DELIVERED", provider_message_id=resp.headers.get("Location") if hasattr(resp, "headers") else None
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("webpush_failed", error_type=type(exc).__name__)
            return DeliveryResult("FAILED", error_code=type(exc).__name__)


class EmailChannel:
    name = "EMAIL"

    def __init__(self, host: str, port: int, sender: str, enabled: bool) -> None:
        self.enabled = enabled and bool(host)
        self.host, self.port, self.sender = host, port, sender

    async def deliver(self, target: dict[str, Any], payload: AlertPayload) -> DeliveryResult:
        if not self.enabled:
            return DeliveryResult("SKIPPED", error_code="EMAIL_NOT_CONFIGURED")
        to = target.get("email")
        if not to:
            return DeliveryResult("FAILED", error_code="NO_EMAIL")
        try:
            from email.message import EmailMessage

            import aiosmtplib

            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = (
                self.sender,
                to,
                f"[Smart Travel] {payload.action_code} — {payload.title_key}",
            )
            msg.set_content(
                "Your trip recommendation changed. Open the app for details (no location data in this email)."
            )
            await aiosmtplib.send(msg, hostname=self.host, port=self.port, timeout=10)
            return DeliveryResult("DELIVERED")
        except Exception as exc:  # noqa: BLE001
            log.warning("email_failed", error_type=type(exc).__name__)
            return DeliveryResult("FAILED", error_code=type(exc).__name__)
