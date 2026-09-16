from __future__ import annotations

from pydantic import Field, SecretStr
from sta_common.settings import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "recommendation"
    service_port: int = 8006

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "smart_travel"
    postgres_recommendation_password: SecretStr = SecretStr("")
    postgres_user_override: str | None = Field(default=None, alias="POSTGRES_RECOMMENDATION_USER")
    redis_url: str = "redis://redis:6379/0"

    emergency_directory_path: str = "emergency-directory/sources.yaml"
    pseudonym_salt: SecretStr = SecretStr("local-dev-salt")  # override in production
    subscription_refresh_minutes: int = 15
    reassessment_stream: str = "alert.reassessment.requested"

    vapid_public_key: str = ""
    vapid_private_key: SecretStr = SecretStr("")
    vapid_subject: str = ""
    smtp_host: str = ""
    smtp_port: int = 1025
    email_sender: str = "alerts@smart-travel.local"
    email_enabled: bool = False

    @property
    def db_user(self) -> str:
        return self.postgres_user_override or "sta_recommendation"


def get_settings() -> Settings:
    return Settings()
