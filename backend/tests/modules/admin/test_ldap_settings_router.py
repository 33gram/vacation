"""Интеграционные тесты `/api/admin/ldap-settings*` (T-M1-16, FR-ADMIN-005)."""

from __future__ import annotations

from sqlalchemy import select

from app.main import app
from app.models.ldap_settings import LdapSettings
from app.modules.admin import ldap_settings_service
from app.modules.auth.embedded_mode import is_embedded_mode
from tests.ldap_guard import LDAP_TEST_HOST, LDAP_TEST_PORT, requires_ldap
from tests.modules.auth.factories import bearer_header, make_account, make_employee


def test_ldap_test_request_marks_bind_password_write_only() -> None:
    # NFR-SEC-001: bind_password не должен уходить в OpenAPI-схему (и
    # сгенерированный TS-клиент) как обычная строка.
    schema = app.openapi()["components"]["schemas"]["LdapTestRequest"]
    bind_password_schema = schema["properties"]["bind_password"]
    variants = bind_password_schema.get("anyOf", [bind_password_schema])
    assert any(variant.get("writeOnly") is True for variant in variants)


async def _make_admin(db_session, *, must_change_password: bool = False):
    employee = await make_employee(db_session, email=f"admin{id(object())}@company.local")
    return await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="system_admin",
        must_change_password=must_change_password,
    )


async def _make_employee_account(db_session):
    employee = await make_employee(db_session, email=f"emp{id(object())}@company.local")
    return await make_account(
        db_session, employee_id=employee.id, auth_type="local", role="employee"
    )


# --- RBAC ---------------------------------------------------------------


async def test_get_ldap_settings_requires_system_admin_role(db_session, api_client) -> None:
    account = await _make_employee_account(db_session)

    response = await api_client.get("/api/admin/ldap-settings", headers=bearer_header(account))

    assert response.status_code == 403


async def test_put_ldap_settings_requires_system_admin_role(db_session, api_client) -> None:
    account = await _make_employee_account(db_session)

    response = await api_client.put(
        "/api/admin/ldap-settings",
        json={
            "server_url": "ldap.company.local",
            "port": 389,
            "base_dn": "dc=company,dc=local",
            "bind_dn": "cn=svc,dc=company,dc=local",
            "bind_password": "secret",
        },
        headers=bearer_header(account),
    )

    assert response.status_code == 403


async def test_test_ldap_settings_requires_system_admin_role(db_session, api_client) -> None:
    account = await _make_employee_account(db_session)

    response = await api_client.post(
        "/api/admin/ldap-settings/test",
        json={
            "server_url": "ldap.company.local",
            "port": 389,
            "base_dn": "dc=company,dc=local",
            "bind_dn": "cn=svc,dc=company,dc=local",
            "bind_password": "secret",
        },
        headers=bearer_header(account),
    )

    assert response.status_code == 403


async def test_admin_endpoint_blocked_until_password_changed(db_session, api_client) -> None:
    account = await _make_admin(db_session, must_change_password=True)

    response = await api_client.get("/api/admin/ldap-settings", headers=bearer_header(account))

    assert response.status_code == 403


# --- GET ------------------------------------------------------------------


async def test_get_ldap_settings_returns_null_when_none_configured(db_session, api_client) -> None:
    account = await _make_admin(db_session)

    response = await api_client.get("/api/admin/ldap-settings", headers=bearer_header(account))

    assert response.status_code == 200
    assert response.json() is None


# --- PUT --------------------------------------------------------------------


async def test_put_ldap_settings_creates_and_never_leaks_password(db_session, api_client) -> None:
    """`FR-ADMIN-005/1` (интерфейс настройки всех перечисленных
    параметров: адрес/порт, base DN, служебная учётная запись, атрибут
    сопоставления). `FR-ADMIN-005/2`, половина "явно сохранены с
    предупреждением": сохранение происходит СРАЗУ, без проверки
    соединения (`upsert_ldap_settings` - решение `T-M1-16`, предупреждение
    - это непогашенный статус проверки, см. фронтенд-тест "shows 'ещё не
    проверялось'...")."""
    account = await _make_admin(db_session)

    response = await api_client.put(
        "/api/admin/ldap-settings",
        json={
            "server_url": "ldap.company.local",
            "port": 389,
            "base_dn": "dc=company,dc=local",
            "bind_dn": "cn=svc,dc=company,dc=local",
            "bind_password": "S3cr3tServiceAccountPassword",
            "user_match_attribute": "mail",
            "is_active": False,
        },
        headers=bearer_header(account),
    )

    assert response.status_code == 200
    body = response.json()
    assert "bind_password" not in body
    assert "bind_password_encrypted" not in body
    assert "S3cr3tServiceAccountPassword" not in response.text

    # И в самой БД пароль хранится не в открытом виде.
    row = (await db_session.execute(select(LdapSettings))).scalars().one()
    assert row.bind_password_encrypted != "S3cr3tServiceAccountPassword"
    assert "S3cr3tServiceAccountPassword" not in row.bind_password_encrypted


async def test_put_ldap_settings_without_password_keeps_existing_one(
    db_session, api_client
) -> None:
    account = await _make_admin(db_session)
    await api_client.put(
        "/api/admin/ldap-settings",
        json={
            "server_url": "ldap.company.local",
            "port": 389,
            "base_dn": "dc=company,dc=local",
            "bind_dn": "cn=svc,dc=company,dc=local",
            "bind_password": "OriginalPassword123",
        },
        headers=bearer_header(account),
    )
    original_row = (await db_session.execute(select(LdapSettings))).scalars().one()
    original_encrypted = original_row.bind_password_encrypted

    response = await api_client.put(
        "/api/admin/ldap-settings",
        json={
            "server_url": "ldap2.company.local",
            "port": 636,
            "base_dn": "dc=company,dc=local",
            "bind_dn": "cn=svc,dc=company,dc=local",
        },
        headers=bearer_header(account),
    )

    assert response.status_code == 200
    assert response.json()["server_url"] == "ldap2.company.local"
    updated_row = (await db_session.execute(select(LdapSettings))).scalars().one()
    assert updated_row.bind_password_encrypted == original_encrypted


# --- POST /test -------------------------------------------------------------


@requires_ldap
async def test_test_ldap_settings_succeeds_and_does_not_persist(db_session, api_client) -> None:
    account = await _make_admin(db_session)

    response = await api_client.post(
        "/api/admin/ldap-settings/test",
        json={
            "server_url": LDAP_TEST_HOST,
            "port": LDAP_TEST_PORT,
            "base_dn": "dc=company,dc=local",
            "bind_dn": "cn=admin,dc=company,dc=local",
            "bind_password": "changeme",
        },
        headers=bearer_header(account),
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert (await db_session.execute(select(LdapSettings))).scalars().first() is None


@requires_ldap
async def test_test_ldap_settings_reports_failure_without_persisting(
    db_session, api_client
) -> None:
    """`FR-ADMIN-005/1` (проверка соединения без сохранения
    некорректных параметров - запрос с неверным паролем ничего не
    сохраняет, `LdapSettings` в БД по-прежнему нет)."""
    account = await _make_admin(db_session)

    response = await api_client.post(
        "/api/admin/ldap-settings/test",
        json={
            "server_url": LDAP_TEST_HOST,
            "port": LDAP_TEST_PORT,
            "base_dn": "dc=company,dc=local",
            "bind_dn": "cn=admin,dc=company,dc=local",
            "bind_password": "wrong-password",
        },
        headers=bearer_header(account),
    )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert (await db_session.execute(select(LdapSettings))).scalars().first() is None


async def test_test_saved_settings_with_nothing_saved_returns_400(db_session, api_client) -> None:
    account = await _make_admin(db_session)

    response = await api_client.post(
        "/api/admin/ldap-settings/test", json={}, headers=bearer_header(account)
    )

    assert response.status_code == 400


@requires_ldap
async def test_test_adhoc_params_does_not_touch_saved_settings(db_session, api_client) -> None:
    # FR-ADMIN-005/FR-AUTH-003: проверка черновых параметров, отличных от
    # сохранённых, не должна помечать сохранённую запись проверенной.
    account = await _make_admin(db_session)
    await api_client.put(
        "/api/admin/ldap-settings",
        json={
            "server_url": LDAP_TEST_HOST,
            "port": LDAP_TEST_PORT,
            "base_dn": "dc=company,dc=local",
            "bind_dn": "cn=admin,dc=company,dc=local",
            "bind_password": "changeme",
            "is_active": True,
        },
        headers=bearer_header(account),
    )

    response = await api_client.post(
        "/api/admin/ldap-settings/test",
        json={
            "server_url": "some-other-host.invalid",
            "port": 389,
            "base_dn": "dc=other,dc=local",
            "bind_dn": "cn=someone,dc=other,dc=local",
            "bind_password": "whatever",
        },
        headers=bearer_header(account),
    )

    assert response.status_code == 200
    row = (await db_session.execute(select(LdapSettings))).scalars().one()
    assert row.last_tested_at is None
    assert row.last_test_status is None


# --- POST /test с частичным телом -----------------------------------------
# Все тесты выше передают либо пустое тело, либо полное — ветка
# "недостающие поля берутся из сохранённой записи" (включая расшифровку
# сохранённого пароля) не была покрыта ничем.


@requires_ldap
async def test_test_draft_params_with_only_server_url_falls_back_to_saved(
    db_session, api_client
) -> None:
    # Сохранённая запись — рабочие реквизиты тестового LDAP. В теле
    # запроса — только `server_url` (совпадающий с сохранённым): порт,
    # `base_dn`, `bind_dn` и пароль обязаны быть подставлены из
    # сохранённой записи, иначе `_test_draft_ldap_params` не соберёт
    # полный набор параметров и вернёт 400, а не 200 success=True.
    account = await _make_admin(db_session)
    await api_client.put(
        "/api/admin/ldap-settings",
        json={
            "server_url": LDAP_TEST_HOST,
            "port": LDAP_TEST_PORT,
            "base_dn": "dc=company,dc=local",
            "bind_dn": "cn=admin,dc=company,dc=local",
            "bind_password": "changeme",
            "is_active": True,
        },
        headers=bearer_header(account),
    )

    response = await api_client.post(
        "/api/admin/ldap-settings/test",
        json={"server_url": LDAP_TEST_HOST},
        headers=bearer_header(account),
    )

    assert response.status_code == 200
    assert response.json()["success"] is True


@requires_ldap
async def test_test_draft_params_with_only_password_overrides_saved(
    db_session, api_client, monkeypatch
) -> None:
    # Сохранённый пароль — заведомо неверный. В теле запроса — только
    # верный пароль. Успех подтверждает, что тело победило сохранённое
    # значение; `decrypt` не должен вызываться вовсе — ленивая
    # расшифровка не должна трогать сохранённый пароль, когда тело
    # запроса его не запрашивает.
    decrypt_calls: list[str] = []
    original_decrypt = ldap_settings_service.decrypt

    def _tracking_decrypt(ciphertext: str, key: str) -> str:
        decrypt_calls.append(ciphertext)
        return original_decrypt(ciphertext, key)

    monkeypatch.setattr(ldap_settings_service, "decrypt", _tracking_decrypt)

    account = await _make_admin(db_session)
    await api_client.put(
        "/api/admin/ldap-settings",
        json={
            "server_url": LDAP_TEST_HOST,
            "port": LDAP_TEST_PORT,
            "base_dn": "dc=company,dc=local",
            "bind_dn": "cn=admin,dc=company,dc=local",
            "bind_password": "wrong-saved-password",
            "is_active": True,
        },
        headers=bearer_header(account),
    )
    decrypt_calls.clear()  # сброс вызова из PUT (шифрование, не расшифровка, но на всякий случай)

    response = await api_client.post(
        "/api/admin/ldap-settings/test",
        json={"bind_password": "changeme"},
        headers=bearer_header(account),
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert decrypt_calls == []


async def test_test_draft_params_with_partial_body_and_nothing_saved_returns_400(
    db_session, api_client
) -> None:
    # Ничего не сохранено, тело частичное (не пустое) — недостающие поля
    # не из чего брать. Обязана быть внятная ошибка (400), а не падение
    # (500) на `saved_row is None` в фолбэках `_test_draft_ldap_params`.
    account = await _make_admin(db_session)

    response = await api_client.post(
        "/api/admin/ldap-settings/test",
        json={"server_url": "ldap.company.local"},
        headers=bearer_header(account),
    )

    assert response.status_code == 400


@requires_ldap
async def test_test_saved_settings_persists_success_and_ends_embedded_mode(
    db_session, api_client
) -> None:
    account = await _make_admin(db_session)
    await api_client.put(
        "/api/admin/ldap-settings",
        json={
            "server_url": LDAP_TEST_HOST,
            "port": LDAP_TEST_PORT,
            "base_dn": "dc=company,dc=local",
            "bind_dn": "cn=admin,dc=company,dc=local",
            "bind_password": "changeme",
            "is_active": True,
        },
        headers=bearer_header(account),
    )

    response = await api_client.post(
        "/api/admin/ldap-settings/test", json={}, headers=bearer_header(account)
    )

    assert response.status_code == 200
    assert response.json()["success"] is True

    get_response = await api_client.get(
        "/api/admin/ldap-settings", headers=bearer_header(account)
    )
    body = get_response.json()
    assert body["last_test_status"] == "success"
    assert body["last_tested_at"] is not None

    assert await is_embedded_mode(db_session) is False


@requires_ldap
async def test_test_saved_settings_persists_failure_and_keeps_embedded_mode(
    db_session, api_client
) -> None:
    account = await _make_admin(db_session)
    await api_client.put(
        "/api/admin/ldap-settings",
        json={
            "server_url": LDAP_TEST_HOST,
            "port": LDAP_TEST_PORT,
            "base_dn": "dc=company,dc=local",
            "bind_dn": "cn=admin,dc=company,dc=local",
            "bind_password": "wrong-password",
            "is_active": True,
        },
        headers=bearer_header(account),
    )

    response = await api_client.post(
        "/api/admin/ldap-settings/test", json={}, headers=bearer_header(account)
    )

    assert response.status_code == 200
    assert response.json()["success"] is False

    get_response = await api_client.get(
        "/api/admin/ldap-settings", headers=bearer_header(account)
    )
    body = get_response.json()
    assert body["last_test_status"] == "failure"
    assert body["last_tested_at"] is not None

    assert await is_embedded_mode(db_session) is True


@requires_ldap
async def test_editing_saved_settings_resets_verification_status(db_session, api_client) -> None:
    # FR-AUTH-003: правка ранее подтверждённых параметров должна снова
    # потребовать проверки, а не наследовать старый 'success'.
    account = await _make_admin(db_session)
    await api_client.put(
        "/api/admin/ldap-settings",
        json={
            "server_url": LDAP_TEST_HOST,
            "port": LDAP_TEST_PORT,
            "base_dn": "dc=company,dc=local",
            "bind_dn": "cn=admin,dc=company,dc=local",
            "bind_password": "changeme",
            "is_active": True,
        },
        headers=bearer_header(account),
    )
    await api_client.post(
        "/api/admin/ldap-settings/test", json={}, headers=bearer_header(account)
    )
    assert await is_embedded_mode(db_session) is False

    response = await api_client.put(
        "/api/admin/ldap-settings",
        json={
            "server_url": "some-other-host.invalid",
            "port": 389,
            "base_dn": "dc=company,dc=local",
            "bind_dn": "cn=admin,dc=company,dc=local",
            "is_active": True,
        },
        headers=bearer_header(account),
    )

    assert response.status_code == 200
    assert response.json()["last_test_status"] is None
    assert await is_embedded_mode(db_session) is True
