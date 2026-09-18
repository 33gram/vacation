"""Интеграционные тесты `/api/auth/*` (T-M1-13). Критерии — FR-AUTH-001,
002, 003, 004, 005 (docs/requirements.md, раздел 8.12)."""

from __future__ import annotations

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.models.ldap_settings import LdapSettings
from tests.ldap_guard import LDAP_TEST_HOST, LDAP_TEST_PORT, requires_ldap
from tests.modules.auth.factories import bearer_header, make_account, make_employee

PASSWORD = "CorrectHorseBatteryStaple"


def _assert_denied_with_hr_message(response) -> None:
    # FR-AUTH-004 требует показывать при отказе сообщение с указанием
    # обратиться к HR-администратору — текст здесь и есть критерий
    # приёмки, поэтому проверяется он, а не только код ответа.
    assert response.status_code == 403
    assert "HR-администратор" in response.json()["detail"]


async def _get_me_and_admin_ldap_settings(api_client, headers):
    me_response = await api_client.get("/api/auth/me", headers=headers)
    admin_response = await api_client.get("/api/admin/ldap-settings", headers=headers)
    return me_response, admin_response


# --- POST /api/auth/login/local ---------------------------------------


async def test_login_local_succeeds(db_session, api_client) -> None:
    employee = await make_employee(db_session, email="ivanov@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
    )

    response = await api_client.post(
        "/api/auth/login/local", json={"login": "ivanov@company.local", "password": PASSWORD}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["role"] == "employee"
    assert body["must_change_password"] is False


async def test_login_local_rejects_empty_password_before_lookup(db_session, api_client) -> None:
    response = await api_client.post(
        "/api/auth/login/local", json={"login": "no-such-user@company.local", "password": ""}
    )

    assert response.status_code == 401


async def test_login_local_rejects_whitespace_password(db_session, api_client) -> None:
    employee = await make_employee(db_session, email="petrov@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
    )

    response = await api_client.post(
        "/api/auth/login/local", json={"login": "petrov@company.local", "password": "   "}
    )

    assert response.status_code == 401


async def test_login_local_unknown_login_returns_401(db_session, api_client) -> None:
    response = await api_client.post(
        "/api/auth/login/local",
        json={"login": "no-such-user@company.local", "password": PASSWORD},
    )

    assert response.status_code == 401


async def test_login_local_deactivated_employee_returns_403_with_hr_message(
    db_session, api_client
) -> None:
    employee = await make_employee(
        db_session, email="terminated@company.local", employment_status="terminated"
    )
    await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
    )

    response = await api_client.post(
        "/api/auth/login/local", json={"login": "terminated@company.local", "password": PASSWORD}
    )

    _assert_denied_with_hr_message(response)


async def test_login_local_disabled_account_returns_403_with_hr_message(
    db_session, api_client
) -> None:
    employee = await make_employee(db_session, email="disabled@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
        is_active=False,
    )

    response = await api_client.post(
        "/api/auth/login/local", json={"login": "disabled@company.local", "password": PASSWORD}
    )

    _assert_denied_with_hr_message(response)


# --- POST /api/auth/login (FR-AUTH-003: embedded mode dispatch) -------


async def test_login_uses_local_auth_in_embedded_mode(db_session, api_client) -> None:
    employee = await make_employee(db_session, email="embedded@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
    )

    response = await api_client.post(
        "/api/auth/login", json={"login": "embedded@company.local", "password": PASSWORD}
    )

    assert response.status_code == 200


async def test_login_rejects_empty_password_before_ldap_bind(db_session, api_client) -> None:
    response = await api_client.post(
        "/api/auth/login", json={"login": "ivanov@company.local", "password": ""}
    )

    assert response.status_code == 401


@requires_ldap
async def test_login_uses_ldap_when_active_settings_exist(db_session, api_client) -> None:
    settings_row = LdapSettings(
        server_url=LDAP_TEST_HOST,
        port=LDAP_TEST_PORT,
        base_dn="dc=company,dc=local",
        bind_dn="cn=admin,dc=company,dc=local",
        bind_password_encrypted=encrypt(
            "changeme", key=get_settings().ldap_password_encryption_key
        ),
        user_match_attribute="mail",
        is_active=True,
        # FR-AUTH-003: переключение на LDAP требует не только is_active,
        # но и подтверждённой проверки подключения.
        last_test_status="success",
    )
    db_session.add(settings_row)
    await db_session.flush()
    employee = await make_employee(db_session, email="ivanov@company.local")
    await make_account(db_session, employee_id=employee.id, auth_type="ldap", role="employee")

    response = await api_client.post(
        "/api/auth/login", json={"login": "ivanov@company.local", "password": "TestPass123!"}
    )

    assert response.status_code == 200


async def test_login_dispatches_to_local_for_local_auth_employee_even_with_ldap_active(
    db_session, api_client
) -> None:
    # T-M1-32/FR-AUTH-003: LDAP настроен и подтверждён (`last_test_status
    # = "success"`), но у этого сотрудника явно выбран локальный пароль
    # — основной `/api/auth/login` обязан обслужить его локально, а не
    # заставлять идти через `/api/auth/login/local`. Не требует
    # `requires_ldap`/реального каталога: `authenticate_local` вообще не
    # обращается к LDAP.
    settings_row = LdapSettings(
        server_url=LDAP_TEST_HOST,
        port=LDAP_TEST_PORT,
        base_dn="dc=company,dc=local",
        bind_dn="cn=admin,dc=company,dc=local",
        bind_password_encrypted=encrypt(
            "changeme", key=get_settings().ldap_password_encryption_key
        ),
        user_match_attribute="mail",
        is_active=True,
        last_test_status="success",
    )
    db_session.add(settings_row)
    await db_session.flush()
    employee = await make_employee(db_session, email="local-despite-ldap@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
    )

    response = await api_client.post(
        "/api/auth/login",
        json={"login": "local-despite-ldap@company.local", "password": PASSWORD},
    )

    assert response.status_code == 200


async def test_login_stays_local_when_active_settings_not_yet_verified(
    db_session, api_client
) -> None:
    # FR-AUTH-003: is_active=true без успешной проверки не должен уводить
    # служебные/обычные учётные записи от локального входа в неработающий
    # каталог.
    settings_row = LdapSettings(
        server_url="unreachable.invalid",
        port=389,
        base_dn="dc=company,dc=local",
        bind_dn="cn=admin,dc=company,dc=local",
        bind_password_encrypted=encrypt(
            "changeme", key=get_settings().ldap_password_encryption_key
        ),
        user_match_attribute="mail",
        is_active=True,
        last_test_status=None,
    )
    db_session.add(settings_row)
    await db_session.flush()
    employee = await make_employee(db_session, email="still-local@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
    )

    response = await api_client.post(
        "/api/auth/login", json={"login": "still-local@company.local", "password": PASSWORD}
    )

    assert response.status_code == 200


# --- POST /api/auth/password/change -----------------------------------


async def test_password_change_succeeds(db_session, api_client) -> None:
    employee = await make_employee(db_session, email="change-me@company.local")
    account = await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
        must_change_password=True,
    )

    response = await api_client.post(
        "/api/auth/password/change",
        json={"current_password": PASSWORD, "new_password": "BrandNewPassword123"},
        headers=bearer_header(account),
    )

    assert response.status_code == 204
    await db_session.refresh(account)
    assert account.must_change_password is False


async def test_password_change_wrong_current_password_returns_401(db_session, api_client) -> None:
    employee = await make_employee(db_session, email="wrong-current@company.local")
    account = await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
    )

    response = await api_client.post(
        "/api/auth/password/change",
        json={"current_password": "wrong", "new_password": "BrandNewPassword123"},
        headers=bearer_header(account),
    )

    assert response.status_code == 401


async def test_password_change_rejects_weak_new_password(db_session, api_client) -> None:
    employee = await make_employee(db_session, email="weak-pw@company.local")
    account = await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
    )

    response = await api_client.post(
        "/api/auth/password/change",
        json={"current_password": PASSWORD, "new_password": "short"},
        headers=bearer_header(account),
    )

    assert response.status_code == 400


async def test_password_change_rejects_ldap_only_account(db_session, api_client) -> None:
    employee = await make_employee(db_session, email="ldap-only@company.local")
    account = await make_account(
        db_session, employee_id=employee.id, auth_type="ldap", role="employee"
    )

    response = await api_client.post(
        "/api/auth/password/change",
        json={"current_password": "anything", "new_password": "BrandNewPassword123"},
        headers=bearer_header(account),
    )

    assert response.status_code == 400


# --- GET /api/auth/me ---------------------------------------------------


async def test_me_reports_embedded_mode_true_without_active_ldap_settings(
    db_session, api_client
) -> None:
    employee = await make_employee(db_session, email="me-embedded@company.local")
    account = await make_account(
        db_session, employee_id=employee.id, auth_type="local", role="employee"
    )

    response = await api_client.get("/api/auth/me", headers=bearer_header(account))

    assert response.status_code == 200
    assert response.json()["embedded_mode"] is True


async def test_me_reports_embedded_mode_true_with_active_but_unverified_ldap_settings(
    db_session, api_client
) -> None:
    # FR-AUTH-003: сохранённая, но не проверенная запись не выводит из
    # режима внедрения — баннер T-M1-21 должен оставаться.
    db_session.add(
        LdapSettings(
            server_url="openldap",
            port=1389,
            base_dn="dc=company,dc=local",
            bind_dn="cn=admin,dc=company,dc=local",
            bind_password_encrypted=encrypt(
                "changeme", key=get_settings().ldap_password_encryption_key
            ),
            is_active=True,
        )
    )
    await db_session.flush()
    employee = await make_employee(db_session, email="me-ldap-unverified@company.local")
    account = await make_account(
        db_session, employee_id=employee.id, auth_type="local", role="employee"
    )

    response = await api_client.get("/api/auth/me", headers=bearer_header(account))

    assert response.status_code == 200
    assert response.json()["embedded_mode"] is True


async def test_me_reports_embedded_mode_false_with_verified_ldap_settings(
    db_session, api_client
) -> None:
    db_session.add(
        LdapSettings(
            server_url="openldap",
            port=1389,
            base_dn="dc=company,dc=local",
            bind_dn="cn=admin,dc=company,dc=local",
            bind_password_encrypted=encrypt(
                "changeme", key=get_settings().ldap_password_encryption_key
            ),
            is_active=True,
            last_test_status="success",
        )
    )
    await db_session.flush()
    employee = await make_employee(db_session, email="me-ldap@company.local")
    account = await make_account(
        db_session, employee_id=employee.id, auth_type="local", role="employee"
    )

    response = await api_client.get("/api/auth/me", headers=bearer_header(account))

    assert response.status_code == 200
    assert response.json()["embedded_mode"] is False


async def test_me_requires_authentication(api_client) -> None:
    response = await api_client.get("/api/auth/me")

    assert response.status_code == 401


# --- FR-AUTH-004 on every request, not only at login ---------------------


async def test_deactivated_employee_card_denies_every_request_not_only_login(
    db_session, api_client
) -> None:
    # Токен валиден, `accounts.is_active` не трогали — только карточку
    # сотрудника переводим в неактивный статус *после* выдачи токена, как
    # при увольнении. И `GET /api/auth/me` (голый `get_current_account`),
    # и эндпоинт за `require_roles` должны отказать одинаково.
    employee = await make_employee(db_session, email="fired-mid-session@company.local")
    account = await make_account(
        db_session, employee_id=employee.id, auth_type="local", role="system_admin"
    )
    headers = bearer_header(account)

    employee.employment_status = "terminated"
    await db_session.flush()

    me_response, admin_response = await _get_me_and_admin_ldap_settings(api_client, headers)

    _assert_denied_with_hr_message(me_response)
    _assert_denied_with_hr_message(admin_response)


# Пропущенную карточку (`employee_id`, не указывающий ни на одну строку
# `employees`) на этом уровне не воспроизвести: `accounts.employee_id`
# защищён FK, так что персистентный `Account` физически не может на неё
# ссылаться. Эта ветка `enforce_access` покрыта на уровне
# `test_rbac.py::test_enforce_access_rejects_missing_employee_card`.


async def test_bootstrap_admin_without_employee_card_is_not_denied(
    db_session, api_client
) -> None:
    # Служебная запись без карточки сотрудника (FR-AUTH-005) не должна
    # ломаться проверкой, которая теперь читает `employees` на каждом
    # запросе, — нет ложных отказов.
    account = await make_account(
        db_session, employee_id=None, login="bootstrap-mid-session", role="system_admin"
    )
    headers = bearer_header(account)

    me_response, admin_response = await _get_me_and_admin_ldap_settings(api_client, headers)

    assert me_response.status_code == 200
    assert admin_response.status_code == 200


async def test_active_employee_and_account_are_not_denied(db_session, api_client) -> None:
    # Нет ложных отказов и для обычного активного случая.
    employee = await make_employee(db_session, email="still-active@company.local")
    account = await make_account(
        db_session, employee_id=employee.id, auth_type="local", role="system_admin"
    )
    headers = bearer_header(account)

    me_response, admin_response = await _get_me_and_admin_ldap_settings(api_client, headers)

    assert me_response.status_code == 200
    assert admin_response.status_code == 200


# --- POST /api/auth/logout ----------------------------------------------


async def test_logout_requires_authentication(api_client) -> None:
    response = await api_client.post("/api/auth/logout")

    assert response.status_code == 401


async def test_logout_succeeds_with_valid_token(db_session, api_client) -> None:
    employee = await make_employee(db_session, email="logout@company.local")
    account = await make_account(
        db_session, employee_id=employee.id, auth_type="local", role="employee"
    )

    response = await api_client.post("/api/auth/logout", headers=bearer_header(account))

    assert response.status_code == 204


# --- T-M1-30 scope check --------------------------------------------------


async def test_password_reset_request_endpoint_does_not_exist(api_client) -> None:
    """T-M1-30: восстановление пароля по e-mail — отдельная задача, не эта
    ветка. Эндпоинт не должен существовать ни в каком виде (не 501/200)."""
    response = await api_client.post(
        "/api/auth/password/reset-request", json={"login": "someone@company.local"}
    )

    assert response.status_code == 404
