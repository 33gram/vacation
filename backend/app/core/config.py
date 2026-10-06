from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    tz: str = "Europe/Moscow"

    # Части подключения (см. .env.example) — из них собираются database_url/
    # redis_url, если не задан полный override. Имена полей database_url и
    # redis_url не менять: на них завязаны app/db/session.py и alembic/env.py.
    postgres_user: str = "vacation_app"
    postgres_password: str = "changeme"
    postgres_db: str = "vacation_tracker"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    database_url: str = ""

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_url: str = ""

    jwt_secret_key: str = "changeme-generate-a-random-secret"
    jwt_algorithm: str = "HS256"
    jwt_access_token_ttl_minutes: int = 60

    ldap_password_encryption_key: str = "changeme-32-byte-encryption-key"

    bootstrap_admin_login: str = "admin"
    bootstrap_admin_password: str = "changeme"

    smtp_host: str = "smtp.company.local"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "vacation-noreply@company.local"

    cors_origins: str = "http://localhost:5173"

    # Поля, для которых нельзя стартовать со значением по умолчанию вне
    # development/test (NFR-SEC-001) — см. _forbid_default_secrets_outside_dev.
    _SECRET_FIELDS = ("jwt_secret_key", "ldap_password_encryption_key", "bootstrap_admin_password")

    @model_validator(mode="after")
    def _assemble_urls(self) -> "Settings":
        if not self.database_url:
            password = quote_plus(self.postgres_password)
            self.database_url = (
                f"postgresql+asyncpg://{self.postgres_user}:{password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        if not self.redis_url:
            self.redis_url = f"redis://{self.redis_host}:{self.redis_port}/0"
        return self

    @model_validator(mode="after")
    def _forbid_default_secrets_outside_dev(self) -> "Settings":
        if self.environment in {"development", "test"}:
            return self

        fields = type(self).model_fields
        unset = [
            name.upper()
            for name in self._SECRET_FIELDS
            if getattr(self, name) == fields[name].default
        ]
        if unset:
            raise ValueError(
                f"Environment '{self.environment}' must not start with default "
                f"secret values. Set these environment variables: {', '.join(unset)}."
            )
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
