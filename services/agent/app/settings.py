from __future__ import annotations

from pydantic import Field, SecretStr
from sta_common.settings import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "agent"
    service_port: int = 8001

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "smart_travel"
    postgres_agent_password: SecretStr = SecretStr("")
    postgres_user_override: str | None = Field(default=None, alias="POSTGRES_AGENT_USER")
    redis_url: str = "redis://redis:6379/0"

    external_data_service_url: str = "http://external-data:8002"
    data_integration_service_url: str = "http://data-integration:8003"
    risk_knowledge_service_url: str = "http://risk-knowledge:8004"
    decision_service_url: str = "http://decision-engine:8005"
    recommendation_service_url: str = "http://recommendation:8006"

    # budgets (00_GIT_DOCKER_DELIVERY_RULES env + 03 plan)
    max_agent_steps: int = 12
    max_tool_calls: int = 10
    agent_total_timeout_seconds: float = 45.0
    agent_tool_timeout_seconds: float = 12.0
    max_llm_calls: int = 2
    max_estimated_cost_usd: float = 0.05

    # follow-up freshness: reuse a snapshot for informational follow-ups only when younger than this
    followup_reuse_max_age_seconds: int = 15 * 60
    graph_version: str = "1.0.0"
    prompt_version: str = "1.0.0"
    run_state_ttl_seconds: int = 24 * 3600

    openai_api_key: SecretStr = SecretStr("")
    openai_intent_model: str = ""

    @property
    def db_user(self) -> str:
        return self.postgres_user_override or "sta_agent"

    @property
    def db_dsn_psycopg(self) -> str:
        pw = self.postgres_agent_password.get_secret_value()
        return f"postgresql://{self.db_user}:{pw}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"


def get_settings() -> Settings:
    return Settings()
