from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import PositiveInt, model_validator
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
    # test`) — МЕХАНИЗМ один, риск для двух путей не одинаков:
    # `authenticate` открывает ДВЕ отдельные `Connection` (bind сервисной
    # записью + поиск, затем bind от имени найденного DN), `test_
    # connection` — одну. PR #172 review (п. 1): предыдущая редакция
    # этого абзаца утверждала «сумма двух соединений, ~10 с, не 5» -
    # ОШИБОЧНО для обоих реальных режимов отказа, измерено прогоном
    # против настоящего тестового openldap (см. докстринг `ldap_client.
    # authenticate` - там разбор по режимам и числа). Короткая версия:
    # при ЗАВИСАНИИ каталога второе соединение не открывается вовсе
    # (первая истёкшая фаза сразу поднимает `LdapAuthError`) - предел
    # ОДНА фаза (5 с при 5/5), не сумма; при МЕДЛЕННОМ, НО ОТВЕЧАЮЩЕМ
    # каталоге предел - сумма РЕАЛЬНЫХ LDAP-обменов (3 при `get_info=
    # "NONE"`, см. ниже), не бюджет одной фазы и не бюджет на весь вызов.
    # Раздельные значения для входа и проверки не введены - сложность
    # второй настройки не оправдана тем, что у одной функции 3 обмена,
    # у другой 2.
    # НЕ запись `business_rules_config` (правило 5, CLAUDE.md) — это не
    # бизнес-правило из раздела 5 требований, а эксплуатационный предел
    # сетевого ожидания; обоснование значения — измеренный риск
    # (ниже), не `NFR-PERF-001` (вход не входит в перечисленные там
    # операции). НЕ колонка `ldap_settings` — настраиваемость через
    # интерфейс администратора была бы изменением `FR-ADMIN-005`, которое
    # правится в требованиях ДО кода (правило 3), не этой задачей.
    # Целые секунды, не float: `ldap3==2.9.1`'s `receive_timeout` требует
    # int - float роняет сокетный вызов с `struct.error: required
    # argument is not an integer` (воспроизведено прогоном против
    # настоящего тестового каталога; `connect_timeout` на `Server`
    # принимает float МОЛЧА, без ошибки — асимметрия между полями,
    # подтверждена прогоном, но оба сделаны int ради единообразия и
    # чтобы не держать этот нюанс в голове у вызывающей стороны).
    #
    # PositiveInt (>=1), не просто int - проверено прогоном против
    # настоящего тестового каталога: `0`/`-1` ПРОХОДЯТ валидацию `int` и
    # ломаются только на самом вызове, причём по-разному и оба тихо.
    # `receive_timeout=0` даёт немедленный `LDAPSocketReceiveError` с
    # `EAGAIN` ("Resource temporarily unavailable" — номер ошибки
    # платформенный, 11 на Linux и 35 на macOS/BSD, называть его по
    # номеру было бы неверно на половине машин разработки) - любой вход
    # через каталог отказывает, неотличимо от неверного пароля (оба -
    # `LdapAuthError`), без единого предупреждения о причине.
    # `receive_timeout=-1`/`connect_timeout=-1` дают `ValueError: Timeout
    # value out of range` - `ValueError` НЕ подкласс `LDAPException`,
    # перехват в `ldap_client.py` его не ловит, и наружу уходит
    # необработанное исключение (500) вместо единообразного отказа
    # входа. `connect_timeout=0` против настоящего каталога прошёл
    # успешно (сеть локальная и быстрая, не успевает проявиться) - не
    # довод в пользу того, что `0` безопасен, только что эта конкретная
    # сеть не вскрыла проблему; `PositiveInt` закрывает оба поля сразу,
    # не оставляя разбираться, какое именно значение на какой сети
    # сработает. Недопустимое значение теперь роняет старт приложения
    # (pydantic ValidationError) с внятным сообщением, а не каждый вход.
    # Через переменные окружения оба случая (дробное значение, `0`/
    # отрицательное) недостижимы — `PositiveInt` отклоняет дробную
    # строку так же, как `0`/`-1`, до того как значение попадёт в `ldap3`.
    #
    # `ldap_receive_timeout_seconds` снижен с 10 до 5 прошлым кругом
    # (PR #172 review) из опасения, что при 5/10 предельное время
    # зависания могло достигать ~20 с; этот круг измерил ЗАВИСАНИЕ прямо
    # (см. докстринг `authenticate`) и нашёл, что оно ограничено ОДНОЙ
    # фазой независимо от того, 5 или 10 стоит у второго параметра -
    # второе соединение при зависании не открывается, суммы не
    # возникает. Опасение, обосновавшее снижение, не подтвердилось, но
    # само значение `5` не возвращено к `10`: симметрия с `connect_
    # timeout_seconds` ничего не стоит, а вторая находка этого же круга
    # (п. 2, `get_info="NONE"` в `ldap_client.py`) снизила реальное число
    # обменов при МЕДЛЕННОМ каталоге с 7 до 3 на вызов `authenticate` -
    # именно это, а не величина `receive_timeout_seconds`, определяет
    # предельное время в этом режиме теперь (см. докстринг
    # `authenticate` - там измеренные секунды по обоим режимам).
    ldap_connect_timeout_seconds: PositiveInt = 5
    ldap_receive_timeout_seconds: PositiveInt = 5

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
