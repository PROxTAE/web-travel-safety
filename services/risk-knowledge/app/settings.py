from __future__ import annotations

from pydantic import Field, SecretStr
from sta_common.settings import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "risk-knowledge"
    service_port: int = 8004

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "smart_travel"
    postgres_knowledge_password: SecretStr = SecretStr("")
    postgres_user_override: str | None = Field(default=None, alias="POSTGRES_KNOWLEDGE_USER")
    qdrant_url: str = "http://qdrant:6333"

    # versioned config files
    thresholds_path: str = "config/thresholds.yaml"
    overrides_path: str = "config/overrides.yaml"
    model_acceptance_path: str = "config/model_acceptance.yaml"
    risk_model_artifact_dir: str = "artifacts"
    feature_schema_path: str = "../data-integration/config/feature_schema.yaml"

    # knowledge
    knowledge_sources_path: str = "knowledge/sources.yaml"
    knowledge_cache_dir: str = "knowledge/cache"
    knowledge_collection_prefix: str = "sta_knowledge"
    embedding_model_name: str = "intfloat/multilingual-e5-small"
    embedding_provider: str = "sentence_transformers"  # or "hashing" (tests only; refused outside test env)
    retrieval_top_k: int = 5
    retrieval_min_score: float = 0.30
    rerank_cross_encoder: bool = False

    # routes
    route_ranking_version: str = "1.0.0"
    materially_safer_delta: float = 0.15
    max_extra_duration_ratio: float = 0.5

    data_integration_url: str = "http://data-integration:8003"

    @property
    def db_user(self) -> str:
        return self.postgres_user_override or "sta_knowledge"


def get_settings() -> Settings:
    return Settings()
