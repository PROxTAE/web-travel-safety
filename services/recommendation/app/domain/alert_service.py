"""Alert evaluation + delivery: compare previous/new recommendation, apply rules, dedup, cooldown, deliver."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any
from uuid import UUID

from sta_common.logging import get_logger
from sta_contracts.enums import SEVERITY_RANK, Severity
from sta_contracts.models import AlertSubscription, RecommendationResponse

from app.domain.alerts import ChangeAssessment, assess_change, cooldown_allows, next_cooldown
from app.notifications.channels import AlertPayload, Channel
from app.repositories.repo import Repository

log = get_logger("alerts")


@dataclass(slots=True)
class DeliveryDecision:
    subscription_id: UUID
    channel: str
    # DELIVERED | SUPPRESSED_COOLDOWN | SUPPRESSED_DUPLICATE | SUPPRESSED_THRESHOLD | NO_CHANGE | FAILED | SKIPPED
    decision: str
    reasons: list[str]
    severity: str
    event_hash: str


def _decision(sub: AlertSubscription, change: ChangeAssessment, decision: str) -> DeliveryDecision:
    return DeliveryDecision(
        subscription_id=sub.id,
        channel=sub.channel.value,
        decision=decision,
        reasons=change.reasons,
        severity=change.severity.value,
        event_hash=change.event_hash,
    )


class AlertService:
    def __init__(self, repo: Repository, channels: dict[str, Channel]) -> None:
        self.repo = repo
        self.channels = channels

    async def evaluate(
        self, previous: RecommendationResponse | None, new: RecommendationResponse
    ) -> list[DeliveryDecision]:
        out: list[DeliveryDecision] = []
        subs = await self.repo.active_subscriptions_for_trip(new.trip_id)
        now = datetime.now(UTC)
        for sub, user, target, _last in subs:
            change = assess_change(previous, new)

            mk = partial(_decision, sub, change)

            if not change.meaningful:
                out.append(mk("NO_CHANGE"))
                continue
            if SEVERITY_RANK[change.severity] < SEVERITY_RANK[sub.severity_threshold] and not change.escalation:
                out.append(mk("SUPPRESSED_THRESHOLD"))
                continue
            if not cooldown_allows(change.severity, change.escalation, sub.cooldown_until, now):
                out.append(mk("SUPPRESSED_COOLDOWN"))
                continue
            if not await self.repo.claim_delivery(sub.id, change.event_hash, sub.channel.value):
                out.append(mk("SUPPRESSED_DUPLICATE"))
                continue
            channel = self.channels.get(sub.channel.value)
            payload = AlertPayload(
                subscription_id=str(sub.id),
                trip_id=str(new.trip_id),
                recommendation_id=str(new.recommendation_id),
                action_code=new.action_code.value,
                risk_level=new.risk_level.value,
                severity=change.severity.value,
                title_key=change.title_key,
                reasons=change.reasons,
                locale=sub.locale,
            )
            if channel is None:
                await self.repo.finish_delivery(
                    sub.id, change.event_hash, sub.channel.value, "SKIPPED", None, "CHANNEL_NOT_CONFIGURED"
                )
                out.append(mk("SKIPPED"))
                continue
            result = await channel.deliver({**target, "user_pseudonym": user}, payload)
            await self.repo.finish_delivery(
                sub.id,
                change.event_hash,
                sub.channel.value,
                result.status,
                result.provider_message_id,
                result.error_code,
            )
            if result.status == "DELIVERED":
                await self.repo.mark_subscription(
                    sub.id,
                    cooldown_until=next_cooldown(change.severity, now),
                    last_recommendation_id=new.recommendation_id,
                )
            out.append(mk(result.status))
            log.info(
                "alert_delivery",
                subscription_id=str(sub.id),
                channel=sub.channel.value,
                status=result.status,
                severity=change.severity.value,
                escalation=change.escalation,
            )
        return out


def severity_of(value: str) -> Severity:
    return Severity(value)


__all__ = ["AlertService", "DeliveryDecision", "severity_of", "Any"]
