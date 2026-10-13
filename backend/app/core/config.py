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

    # T-M1-58: бюджет ожидания одного обращения к LDAP-каталогу (connect
    # и приём ответа — `ldap3`'s `connect_timeout`/`receive_timeout`, оба
    # `None` по умолчанию у самой библиотеки, то есть без ограничения).
    # Общий для входа пользователя (`ldap_service.authenticate_via_ldap`)
    # и администраторской проверки (`ldap_settings_service._run_ldap_
    # test`) — оба делают один и тот же вид операции (bind + search) к
    # одному и тому же каталогу, и риск, от которого бюджет защищает
    # (поток воркера заблокирован на неотвечающем каталоге), для обоих
    # путей одинаков; раздельные бюджеты добавили бы две настройки без
    # содержательной причины различаться. НЕ запись `business_rules_
    # config` (правило 5, CLAUDE.md) — это не бизнес-правило из раздела 5
    # требований, а эксплуатационный предел сетевого ожидания. НЕ колонка
    # `ldap_settings` — настраиваемость через интерфейс администратора
    # была бы изменением `FR-ADMIN-005`, которое правится в требованиях
    # ДО кода (правило 3), не этой задачей.
    # Целые секунды, не float: `ldap3==2.9.1`'s `receive_timeout` требует
    # int - float роняет сокетный вызов с `struct.error: required
    # argument is not an integer` (воспроизведено прогоном против
    # настоящего тестового каталога; `connect_timeout` на `Server`
    # принимает float без ошибки, но оба поля сделаны int ради
    # единообразия и чтобы не держать именно этот нюанс в голове у
    # вызывающей стороны).
    ldap_connect_timeout_seconds: int = 5
    ldap_receive_timeout_seconds: int = 10

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
