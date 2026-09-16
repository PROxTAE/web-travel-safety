"""OpenAI Responses API explainer: strict JSON Schema output, store=False, temperature 0, bounded tokens/time,
refusal/incomplete handling, one validated retry, then deterministic fallback."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sta_common.logging import get_logger
from sta_common.metrics import LLM_CALLS, LLM_FALLBACK
from sta_contracts.enums import ActionCode, ReasonCode, RiskLevel

from app.llm.fallback import fallback_explanation
from app.llm.schemas import PROMPT_VERSION, Explanation, explanation_json_schema
from app.llm.validators import ValidationResult, validate_explanation

log = get_logger("llm")


@dataclass(slots=True)
class LlmOutcome:
    explanation: Explanation
    used_fallback: bool
    fallback_reason: str | None
    validation: ValidationResult | None
    model: str | None
    prompt_version: str
    prompt_hash: str
    output_hash: str | None
    latency_ms: float
    tokens_in: int | None
    tokens_out: int | None
    attempts: int


class ExplanationClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        prompts_dir: str | Path,
        service_name: str,
        timeout_seconds: float = 12.0,
        max_output_tokens: int = 700,
        enabled: bool = True,
    ) -> None:
        self.enabled = enabled and bool(api_key) and bool(model)
        self.model = model
        self.timeout = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.service_name = service_name
        self._env = Environment(loader=FileSystemLoader(str(prompts_dir)), undefined=StrictUndefined, autoescape=False)  # noqa: S701 - plain text prompt, not HTML
        self._client: Any = None
        if self.enabled:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)

    def render(self, ctx: dict[str, Any]) -> str:
        return self._env.get_template("v1/explain.j2").render(**ctx)

    async def explain(
        self,
        *,
        action: ActionCode,
        risk_level: RiskLevel,
        confidence: float,
        reason_codes: list[ReasonCode],
        facts: list[str],
        routes: list[str],
        limitations: list[str],
        citations: list[dict[str, str]],
        locale: str,
        question: str | None,
        suggested_delay_minutes: int | None,
        selected_route: str | None,
    ) -> LlmOutcome:
        ctx = {
            "action_code": action.value,
            "risk_level": risk_level.value,
            "confidence": confidence,
            "reason_codes": [c.value for c in reason_codes],
            "facts": facts,
            "routes": routes,
            "limitations": limitations,
            "citations": citations,
            "locale": locale,
            "question": question,
            "suggested_delay_minutes": suggested_delay_minutes,
            "selected_route": selected_route,
        }
        prompt = self.render(ctx)
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        allowed_ids = {c["id"] for c in citations}
        extra_numbers = [str(suggested_delay_minutes or ""), f"{confidence}", *routes]

        def fb(
            reason: str, validation: ValidationResult | None, attempts: int, latency: float, model: str | None
        ) -> LlmOutcome:
            LLM_FALLBACK.labels(self.service_name, reason).inc()
            exp = fallback_explanation(
                action=action,
                risk_level=risk_level,
                reason_codes=reason_codes,
                limitations=limitations,
                locale=locale,
                delay_minutes=suggested_delay_minutes,
            )
            return LlmOutcome(
                exp, True, reason, validation, model, PROMPT_VERSION, prompt_hash, None, latency, None, None, attempts
            )

        if not self.enabled:
            return fb("llm_disabled", None, 0, 0.0, None)

        started = time.perf_counter()
        last_validation: ValidationResult | None = None
        feedback = ""
        for attempt in (1, 2):
            try:
                resp = await asyncio.wait_for(
                    self._client.responses.create(
                        model=self.model,
                        input=[
                            {"role": "system", "content": prompt + feedback},
                            {"role": "user", "content": "Write the explanation JSON now."},
                        ],
                        text={
                            "format": {
                                "type": "json_schema",
                                "name": "travel_safety_explanation",
                                "schema": explanation_json_schema(action.value),
                                "strict": True,
                            }
                        },
                        temperature=0,
                        max_output_tokens=self.max_output_tokens,
                        store=False,
                    ),
                    timeout=self.timeout,
                )
            except TimeoutError:
                LLM_CALLS.labels(self.service_name, "timeout").inc()
                return fb("timeout", last_validation, attempt, (time.perf_counter() - started) * 1000, self.model)
            except Exception as exc:  # noqa: BLE001 - provider error → fallback, never raw output
                LLM_CALLS.labels(self.service_name, "error").inc()
                log.warning("llm_error", error_type=type(exc).__name__)
                return fb(
                    f"error:{type(exc).__name__}",
                    last_validation,
                    attempt,
                    (time.perf_counter() - started) * 1000,
                    self.model,
                )
            latency = (time.perf_counter() - started) * 1000
            status = getattr(resp, "status", "completed")
            if status == "incomplete":
                LLM_CALLS.labels(self.service_name, "incomplete").inc()
                return fb("incomplete_output", last_validation, attempt, latency, self.model)
            refusal = _refusal(resp)
            if refusal:
                LLM_CALLS.labels(self.service_name, "refusal").inc()
                return fb("refusal", last_validation, attempt, latency, self.model)
            text = getattr(resp, "output_text", "") or ""
            try:
                exp = Explanation.model_validate(json.loads(text))
            except Exception:  # noqa: BLE001
                LLM_CALLS.labels(self.service_name, "invalid_json").inc()
                feedback = "\n\nPrevious output was not valid JSON for the schema. Output strictly the JSON object."
                last_validation = ValidationResult(ok=False, schema_ok=False, problems=["invalid json"])
                continue
            v = validate_explanation(
                exp,
                locked_action=action.value,
                allowed_citations=allowed_ids,
                allowed_facts=facts,
                extra_allowed_numbers=extra_numbers,
            )
            last_validation = v
            usage = getattr(resp, "usage", None)
            if v.ok:
                LLM_CALLS.labels(self.service_name, "ok").inc()
                return LlmOutcome(
                    exp,
                    False,
                    None,
                    v,
                    self.model,
                    PROMPT_VERSION,
                    prompt_hash,
                    hashlib.sha256(text.encode()).hexdigest(),
                    latency,
                    getattr(usage, "input_tokens", None),
                    getattr(usage, "output_tokens", None),
                    attempt,
                )
            LLM_CALLS.labels(self.service_name, "validation_failed").inc()
            # retry once with non-sensitive validation feedback
            feedback = (
                "\n\nYour previous answer violated these rules: "
                + "; ".join(p.split(":")[0] for p in v.problems[:4])
                + ". Fix them and output the JSON again."
            )
        return fb("validation_failed", last_validation, 2, (time.perf_counter() - started) * 1000, self.model)


def _refusal(resp: Any) -> str | None:
    for item in getattr(resp, "output", []) or []:
        for part in getattr(item, "content", []) or []:
            if getattr(part, "type", "") == "refusal":
                return str(getattr(part, "refusal", "refused"))
    return None
