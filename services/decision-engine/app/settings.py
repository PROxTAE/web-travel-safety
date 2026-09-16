from __future__ import annotations

from pydantic import Field, SecretStr
from sta_common.settings import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "decision-engine"
    service_port: int = 8005

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "smart_travel"
    postgres_decision_password: SecretStr = SecretStr("")
    postgres_user_override: str | None = Field(default=None, alias="POSTGRES_DECISION_USER")
    redis_url: str = "redis://redis:6379/0"

    policy_path: str = "policies/v1/decision-table.yaml"
    policy_schema_path: str = "schemas/decision-policy.schema.json"
    prompts_dir: str = "prompts"
    supported_feature_schema_versions: str = "1.0.0"

    openai_api_key: SecretStr = SecretStr("")
    openai_explainer_model: str = ""
    openai_timeout_seconds: float = 12.0
    openai_max_output_tokens: int = 700
    llm_enabled: bool = True
    explanation_cache_ttl_seconds: int = 900

    @property
    def db_user(self) -> str:
        return self.postgres_user_override or "sta_decision"

    @property
    def feature_schema_versions(self) -> set[str]:
        return {v.strip() for v in self.supported_feature_schema_versions.split(",") if v.strip()}


def get_settings() -> Settings:
    return Settings()
