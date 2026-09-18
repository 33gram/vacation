"""Интеграционные тесты против тестового OpenLDAP из infra/docker-compose.yml
(T-M1-06/07). Запускать через `make test-backend` (тот же docker-сеть, что
и сервис `openldap`); в CI без этого сервиса модуль пропускается целиком.
"""

import socket

import pytest

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.models.ldap_settings import LdapSettings
from app.modules.auth.exceptions import (
    AccessDeniedError,
    AuthenticationError,
    LdapNotConfiguredError,
)
from app.modules.auth.ldap_service import authenticate_via_ldap
from tests.modules.auth.factories import make_account, make_employee

# Совпадает с сервисом `openldap` в infra/docker-compose.yml: имя хоста —
# внутри docker-сети, порт 1389 — внутренний порт образа bitnami (наружу
# он проброшен как 389, но между контейнерами используется 1389 напрямую).
LDAP_TEST_HOST = "openldap"
LDAP_TEST_PORT = 1389
LDAP_TEST_BASE_DN = "dc=company,dc=local"
LDAP_TEST_ADMIN_DN = "cn=admin,dc=company,dc=local"
LDAP_TEST_ADMIN_PASSWORD = "changeme"
LDAP_TEST_USER_PASSWORD = "TestPass123!"


def _ldap_reachable() -> bool:
    try:
        with socket.create_connection((LDAP_TEST_HOST, LDAP_TEST_PORT), timeout=1.0):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _ldap_reachable(),
    reason=(
        "Тестовый OpenLDAP (infra/docker-compose.yml, сервис openldap) "
        "недоступен — запускать через `make test-backend`"
    ),
)


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
    await _make_active_ldap_settings(db_session)
    employee = await make_employee(db_session, email="ivanov@company.local")
    await make_account(db_session, employee_id=employee.id, auth_type="ldap", role="employee")

    result = await authenticate_via_ldap(
        db_session, "ivanov@company.local", LDAP_TEST_USER_PASSWORD
    )

    assert result.access_token
    assert result.refresh_token


async def test_wrong_password_raises_authentication_error(db_session) -> None:
    await _make_active_ldap_settings(db_session)
    employee = await make_employee(db_session, email="petrov@company.local")
    await make_account(db_session, employee_id=employee.id, auth_type="ldap", role="employee")

    with pytest.raises(AuthenticationError):
        await authenticate_via_ldap(db_session, "petrov@company.local", "wrong-password")


async def test_unknown_ldap_user_raises_authentication_error(db_session) -> None:
    await _make_active_ldap_settings(db_session)

    with pytest.raises(AuthenticationError):
        await authenticate_via_ldap(
            db_session, "no-such-user@company.local", LDAP_TEST_USER_PASSWORD
        )


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
