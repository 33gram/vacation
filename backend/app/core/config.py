from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    tz: str = "Europe/Moscow"

    database_url: str = "postgresql+asyncpg://vacation_app:changeme@localhost:5432/vacation_tracker"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret_key: str = "changeme-generate-a-random-secret"
    jwt_algorithm: str = "HS256"
    jwt_access_token_ttl_minutes: int = 60
    jwt_refresh_token_ttl_days: int = 14

    field_encryption_key: str = "changeme-32-byte-encryption-key"

    bootstrap_admin_login: str = "admin"
    bootstrap_admin_password: str = "changeme"

    smtp_host: str = "smtp.company.local"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "vacation-noreply@company.local"

    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
