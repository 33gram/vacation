"""Интеграционные тесты против тестового OpenLDAP из infra/docker-compose.yml
(T-M1-06/07). Хост/порт настраиваются через LDAP_TEST_HOST/LDAP_TEST_PORT:

- локально через `make test-backend` — тесты выполняются внутри контейнера
  `backend` в той же docker-сети, что и сервис `openldap`, значения по
  умолчанию ("openldap", 1389 — внутренний порт образа bitnami) подходят
  без переопределения;
- в CI (.github/workflows/ci.yml) — `openldap` поднимается отдельным шагом
  через `docker compose ... up -d openldap` на самом раннере, а не как
  сосед по docker-сети, поэтому используются LDAP_TEST_HOST=localhost,
  LDAP_TEST_PORT=389 (порт, который compose публикует наружу: `389:1389`).

Модуль сам пропускает себя, если указанный хост недоступен, — например,
при локальном запуске тестов без поднятого `docker compose` (guard —
`tests/ldap_guard.py`, общий для всех файлов с LDAP-тестами). В CI
пропуск любого теста, включая эти, запрещён на уровне всего прогона
переменной `PYTEST_FORBID_SKIPS=1` (`tests/conftest.py`) — если каталог
там недоступен, прогон падает, а не молча пропускает эти тесты.
"""

import pytest

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.models.ldap_settings import LdapSettings
from app.modules.auth import ldap_service
from app.modules.auth.exceptions import (
    AccessDeniedError,
    AuthenticationError,
    LdapNotConfiguredError,
)
from app.modules.auth.ldap_service import authenticate_via_ldap
from tests.ldap_guard import LDAP_TEST_HOST, LDAP_TEST_PORT, requires_ldap
from tests.ldap_hang_fixtures import black_hole_server
from tests.modules.auth.factories import make_account, make_employee

LDAP_TEST_BASE_DN = "dc=company,dc=local"
LDAP_TEST_ADMIN_DN = "cn=admin,dc=company,dc=local"
LDAP_TEST_ADMIN_PASSWORD = "changeme"
LDAP_TEST_USER_PASSWORD = "TestPass123!"

pytestmark = requires_ldap


async def _make_active_ldap_settings(session) -> LdapSettings:
    settings_row = LdapSettings(
        server_url=LDAP_TEST_HOST,
        port=LDAP_TEST_PORT,
        base_dn=LDAP_TEST_BASE_DN,
        bind_dn=LDAP_TEST_ADMIN_DN,
        bind_password_encrypted=encrypt(
            LDAP_TEST_ADMIN_PASSWORD, key=get_settings().ldap_password_encryption_key
        ),
        user_match_attribute="mail",
        is_active=True,
    )
    session.add(settings_row)
    await session.flush()
    return settings_row


async def test_successful_ldap_login_issues_tokens(db_session) -> None:
    """`FR-AUTH-001/1` (логин и пароль проверяются в настоящем
    корпоративном каталоге - тестовый OpenLDAP). `FR-AUTH-001/2`
    (сопоставление по e-mail - `make_employee(email=...)`/логин
    совпадают; сама роль в открытой сессии проверена отдельно, глубже,
    в `test_rbac.py`, FR-AUTH-004/3 - здесь только факт, что сессия
    вообще открывается после успешного сопоставления)."""
    await _make_active_ldap_settings(db_session)
    employee = await make_employee(db_session, email="ivanov@company.local")
    await make_account(db_session, employee_id=employee.id, auth_type="ldap", role="employee")

    result = await authenticate_via_ldap(
        db_session, "ivanov@company.local", LDAP_TEST_USER_PASSWORD
    )

    assert result.access_token


async def test_wrong_password_raises_authentication_error(db_session) -> None:
    await _make_active_ldap_settings(db_session)
    employee = await make_employee(db_session, email="petrov@company.local")
    await make_account(db_session, employee_id=employee.id, auth_type="ldap", role="employee")

    with pytest.raises(AuthenticationError):
        await authenticate_via_ldap(db_session, "petrov@company.local", "wrong-password")


async def test_unresponsive_directory_raises_authentication_error(
    db_session, monkeypatch
) -> None:
    # T-M1-58: таймаут обращения к каталогу на пути ВХОДА - тот же
    # AuthenticationError, что и неверный пароль, не 500 и не иной
    # текст (FR-AUTH-001). Бюджет укорочен монкипатчем `get_settings`
    # как его видит `ldap_service.py` - только этот тест ждёт недолго,
    # не весь прогон (реальный процесс кэширует `Settings`).
    fast_settings = get_settings().model_copy(
        update={"ldap_connect_timeout_seconds": 1, "ldap_receive_timeout_seconds": 1}
    )
    monkeypatch.setattr(ldap_service, "get_settings", lambda: fast_settings)

    with black_hole_server() as port:
        settings_row = LdapSettings(
            server_url="127.0.0.1",
            port=port,
            base_dn=LDAP_TEST_BASE_DN,
            bind_dn=LDAP_TEST_ADMIN_DN,
            bind_password_encrypted=encrypt(
                LDAP_TEST_ADMIN_PASSWORD, key=fast_settings.ldap_password_encryption_key
            ),
            user_match_attribute="mail",
            is_active=True,
        )
        db_session.add(settings_row)
        await db_session.flush()
        employee = await make_employee(db_session, email="sidorova@company.local")
        await make_account(
            db_session, employee_id=employee.id, auth_type="ldap", role="employee"
        )

        with pytest.raises(AuthenticationError):
            await authenticate_via_ldap(
                db_session, "sidorova@company.local", LDAP_TEST_USER_PASSWORD
            )


async def test_unknown_ldap_user_raises_authentication_error(db_session) -> None:
    await _make_active_ldap_settings(db_session)

    with pytest.raises(AuthenticationError):
        await authenticate_via_ldap(
            db_session, "no-such-user@company.local", LDAP_TEST_USER_PASSWORD
        )


async def test_empty_password_is_rejected_before_ldap_bind(db_session) -> None:
    # RFC 4513: bind с пустым паролем — unauthenticated bind, некоторые
    # каталоги вернут для него успех для любого существующего логина.
    # Отклоняется до обращения к ldap_settings/каталогу (FR-AUTH-004),
    # поэтому тест не создаёт активную запись ldap_settings.
    with pytest.raises(AuthenticationError):
        await authenticate_via_ldap(db_session, "ivanov@company.local", "")


async def test_whitespace_only_password_is_rejected_before_ldap_bind(db_session) -> None:
    with pytest.raises(AuthenticationError):
        await authenticate_via_ldap(db_session, "ivanov@company.local", "   ")


async def test_no_matching_employee_denies_access(db_session) -> None:
    await _make_active_ldap_settings(db_session)

    with pytest.raises(AccessDeniedError):
        await authenticate_via_ldap(
            db_session, "smirnova@company.local", LDAP_TEST_USER_PASSWORD
        )


async def test_deactivated_employee_denies_access(db_session) -> None:
    await _make_active_ldap_settings(db_session)
    employee = await make_employee(
        db_session, email="fedorova@company.local", employment_status="terminated"
    )
    await make_account(db_session, employee_id=employee.id, auth_type="ldap", role="employee")

    with pytest.raises(AccessDeniedError):
        await authenticate_via_ldap(
            db_session, "fedorova@company.local", LDAP_TEST_USER_PASSWORD
        )


async def test_missing_account_denies_access(db_session) -> None:
    await _make_active_ldap_settings(db_session)
    await make_employee(db_session, email="sysadmin@company.local")

    with pytest.raises(AccessDeniedError):
        await authenticate_via_ldap(
            db_session, "sysadmin@company.local", LDAP_TEST_USER_PASSWORD
        )


async def test_embedded_mode_without_active_ldap_settings_raises(db_session) -> None:
    with pytest.raises(LdapNotConfiguredError):
        await authenticate_via_ldap(
            db_session, "ivanov@company.local", LDAP_TEST_USER_PASSWORD
        )
