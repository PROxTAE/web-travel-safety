"""Strict JSON Schema for the explanation (OpenAI Structured Outputs: additionalProperties false, all required)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

PROMPT_VERSION = "1.0.0"


class Explanation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_code: str
    short_summary: str = Field(max_length=600)
    reasons: list[str] = Field(max_length=6)
    immediate_actions: list[str] = Field(max_length=6)
    limitations: list[str] = Field(max_length=8)
    citations_used: list[str] = Field(max_length=10)


def explanation_json_schema(action_code: str) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action_code", "short_summary", "reasons", "immediate_actions", "limitations", "citations_used"],
        "properties": {
            "action_code": {"type": "string", "enum": [action_code]},
            "short_summary": {"type": "string"},
            "reasons": {"type": "array", "items": {"type": "string"}},
            "immediate_actions": {"type": "array", "items": {"type": "string"}},
            "limitations": {"type": "array", "items": {"type": "string"}},
            "citations_used": {"type": "array", "items": {"type": "string"}},
        },
    }
