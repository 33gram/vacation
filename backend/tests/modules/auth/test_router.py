"""Интеграционные тесты `/api/auth/*` (T-M1-13). Критерии — FR-AUTH-001,
002, 003, 004, 005 (docs/requirements.md, раздел 8.12)."""

from __future__ import annotations

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.main import app
from app.models.ldap_settings import LdapSettings
from app.modules.auth import password_policy
from app.modules.auth.password_policy import MAX_PASSWORD_BYTES, VIOLATION_CONTAINS_IDENTITY
from tests.ldap_guard import LDAP_TEST_HOST, LDAP_TEST_PORT, requires_ldap
from tests.modules.auth.factories import bearer_header, make_account, make_employee

PASSWORD = "CorrectHorseBatteryStaple"


def _assert_denied_with_hr_message(response) -> None:
    # FR-AUTH-004 требует показывать при отказе сообщение с указанием
    # обратиться к HR-администратору — текст здесь и есть критерий
    # приёмки, поэтому проверяется он, а не только код ответа.
    assert response.status_code == 403
    assert "HR-администратор" in response.json()["detail"]


def test_password_change_fields_are_marked_write_only() -> None:
    # NFR-SEC-001, симметрично `test_employee_password_fields_are_
    # marked_write_only` (PR #140 review, п. 1 коммита 5): пароль не
    # должен уходить в OpenAPI-схему (и сгенерированный TS-клиент) как
    # обычная строка.
    schema = app.openapi()["components"]["schemas"]["PasswordChangeRequest"]["properties"]
    for field_name in ("current_password", "new_password"):
        assert schema[field_name].get("writeOnly") is True


def test_login_password_field_is_marked_write_only() -> None:
    # PR #140 review, третий круг: `LoginRequest.password` — единственное
    # парольное поле, пропущенное коммитом 4b47556, когда тот объявил
    # асимметрию между схемами закрытой. Проверяется именно схема
    # OpenAPI (`writeOnly`), а не тип в Python — тип мог смениться на
    # `SecretStr` и всё равно не попасть в схему без `Field(...)`.
    schema = app.openapi()["components"]["schemas"]["LoginRequest"]["properties"]
    assert schema["password"].get("writeOnly") is True


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


async def test_login_local_rejects_boundary_password_with_appended_tail(
    db_session, api_client
) -> None:
    """PR #140 review (второй круг), замечание 1, живым HTTP: пароль
    РОВНО на 72-байтовой границе легален и проходит вход как есть. До
    этой правки тот же пароль с ЛЮБЫМ хвостом после байта 72 тоже
    возвращал 200 и токен на ту же учётную запись — `LoginRequest.
    password`'s `max_length=128` ограничивает символы, не байты, а
    `authenticate_local` уходил прямо в `bcrypt.checkpw`, который
    усекает вход так же, как хэширование."""
    boundary_password = "Aa1!" * 18  # ровно 72 ASCII-байта
    employee = await make_employee(db_session, email="boundary-http@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(boundary_password),
    )

    legal = await api_client.post(
        "/api/auth/login/local",
        json={"login": "boundary-http@company.local", "password": boundary_password},
    )
    assert legal.status_code == 200

    with_tail = await api_client.post(
        "/api/auth/login/local",
        json={
            "login": "boundary-http@company.local",
            "password": boundary_password + "EXTRA_TAIL",
        },
    )
    assert with_tail.status_code == 401


async def test_login_local_rejects_cyrillic_password_over_the_byte_boundary(
    db_session, api_client
) -> None:
    # Кириллица — 2 байта на символ в UTF-8: граница в 72 байта достигается
    # на 36 символах, а не на 128 (символьный лимит схемы `LoginRequest.
    # password`). 37-символьный пароль легко проходит символьный лимит, но
    # уже превышает байтовую границу — без байтовой проверки на входе это
    # был бы обход именно для кириллических/многобайтовых паролей.
    boundary_password = "п" * 36  # ровно 72 байта в UTF-8
    assert len(boundary_password.encode("utf-8")) == MAX_PASSWORD_BYTES
    employee = await make_employee(db_session, email="cyrillic-boundary@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(boundary_password),
    )

    legal = await api_client.post(
        "/api/auth/login/local",
        json={"login": "cyrillic-boundary@company.local", "password": boundary_password},
    )
    assert legal.status_code == 200

    with_tail = await api_client.post(
        "/api/auth/login/local",
        json={
            "login": "cyrillic-boundary@company.local",
            "password": boundary_password + "х",  # +2 байта, 37 символов — всё ещё «короткий»
        },
    )
    assert with_tail.status_code == 401


# --- POST /api/auth/login (FR-AUTH-003: embedded mode dispatch) -------


async def test_login_uses_local_auth_in_embedded_mode(db_session, api_client) -> None:
    """`FR-AUTH-003/1` (вход всех пользователей локально, пока LDAP не
    настроен) - предупреждение администратору про режим внедрения
    проверяется отдельно, на фронтенде (`authShell.test.tsx`)."""
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


async def test_login_rejects_empty_password_in_embedded_mode(db_session, api_client) -> None:
    """`FR-AUTH-004/1`, half of it: no LDAP settings exist here, so the
    system is in embedded mode and `/api/auth/login` dispatches to
    `authenticate_local`, not to `authenticate_via_ldap` - renamed from
    `..._before_ldap_bind` (`T-M1-23` review of this branch's own
    citation work): that name claimed to test the LDAP path
    specifically, but with no active settings this request never
    reaches it. See `test_login_rejects_empty_password_before_ldap_
    bind_when_ldap_active` below for the actual LDAP-path case."""
    response = await api_client.post(
        "/api/auth/login", json={"login": "ivanov@company.local", "password": ""}
    )

    assert response.status_code == 401


async def test_login_rejects_empty_password_before_ldap_bind_when_ldap_active(
    db_session, api_client, monkeypatch
) -> None:
    """`FR-AUTH-004/1`, the other half: with LDAP actually active and
    verified (so this request really is dispatched to
    `authenticate_via_ldap`), an empty password is still rejected
    BEFORE any bind is attempted (RFC 4513 unauthenticated bind) -
    proven by patching the bind/search call to raise if it's ever
    invoked, not just by asserting the response code, which an
    embedded-mode short-circuit (as the renamed test above turned out
    to be) would satisfy just as well without ever reaching LDAP."""

    def _fail_if_called(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("ldap_bind_and_search must not run for an empty password")

    monkeypatch.setattr(
        "app.modules.auth.ldap_service.ldap_bind_and_search", _fail_if_called
    )

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

    response = await api_client.post(
        "/api/auth/login", json={"login": "ivanov@company.local", "password": ""}
    )

    assert response.status_code == 401


async def test_login_rejects_oversized_password_before_hashing(api_client) -> None:
    """PR #140 review, ПРОВЕРКА п. 2: запрос с мегабайтным паролем
    отсекается схемой (`LoginRequest.password`'s `max_length`) до
    диспетчеризации на LDAP/локальную аутентификацию — не доходит до
    bcrypt-хэширования вовсе. Нет `db_session`: запрос отклоняется на
    уровне валидации, до первого обращения к БД."""
    oversized_password = "A" * (2 * 1024 * 1024)

    response = await api_client.post(
        "/api/auth/login", json={"login": "ivanov@company.local", "password": oversized_password}
    )

    assert response.status_code == 422


@requires_ldap
async def test_login_uses_ldap_when_active_settings_exist(db_session, api_client) -> None:
    """`FR-AUTH-003/2` (сохранение и успешная проверка переключают способ
    входа по умолчанию на каталог)."""
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
    """`FR-AUTH-003/2` (сохраняя при этом доступ учётных записей с явно
    выбранным локальным паролем). Диспетчеризация (`local_service.
    uses_local_auth`) смотрит только на `accounts.auth_type`, не на то,
    привязана ли запись к карточке сотрудника, - тот же путь обслуживает
    и служебные/административные учётные записи (`FR-AUTH-005`), для
    них отдельного теста с активным LDAP не заведено: код один и тот же,
    а фикстура сотрудника здесь ближе к реальному сценарию, который
    ловит T-M1-32."""
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
    violations = response.json()["detail"]
    assert isinstance(violations, list)
    assert len(violations) > 1
    # T-M1-45: код + русское сообщение (NFR-LOC-001), не голая строка.
    assert all(set(v) == {"code", "message"} for v in violations)


async def test_password_change_rejects_password_containing_employee_full_name(
    db_session, api_client
) -> None:
    """T-M1-38, точка подключения 3 (`auth/router.change_password`):
    контекст берётся у ТЕКУЩЕЙ учётной записи через её карточку
    сотрудника (`_identity_context`), а не передаётся вызывающим."""
    employee = await make_employee(
        db_session, email="ivanov@company.local", full_name="Иванов Иван Иванович"
    )
    account = await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
    )

    response = await api_client.post(
        "/api/auth/password/change",
        json={"current_password": PASSWORD, "new_password": "Ivanov2026!"},
        headers=bearer_header(account),
    )

    assert response.status_code == 400
    assert any(v["code"] == VIOLATION_CONTAINS_IDENTITY for v in response.json()["detail"])


async def test_password_change_rejects_password_containing_service_account_login(
    db_session, api_client
) -> None:
    """Симметрично тесту выше, для второй ветки `_identity_context`:
    служебная запись без карточки (`employee_id is None`) сверяется по
    `login`, а не по e-mail/ФИО, которых у неё нет."""
    account = await make_account(
        db_session,
        login="svc-import",
        auth_type="local",
        role="hr_admin",
        password_hash=hash_password(PASSWORD),
    )

    response = await api_client.post(
        "/api/auth/password/change",
        json={"current_password": PASSWORD, "new_password": "Svc-import1!"},
        headers=bearer_header(account),
    )

    assert response.status_code == 400
    assert any(v["code"] == VIOLATION_CONTAINS_IDENTITY for v in response.json()["detail"])


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
    """`FR-AUTH-004/2` (сообщение с указанием обратиться к
    HR-администратору - проверено дословно через
    `_assert_denied_with_hr_message`)."""
    # Токен валиден, `accounts.is_active` не трогали — только карточку
    # сотрудника переводим в неактивный статус *после* выдачи токена, как
    # при увольнении. И `GET /api/auth/me` (`get_current_account_allowing_
    # password_change`, T-M1-46), и эндпоинт за `require_roles` должны
    # отказать одинаково — `enforce_access` вызывается в обоих случаях.
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


# --- GET /api/auth/password-policy (T-M1-45) -----------------------------


async def test_password_policy_requires_authentication(api_client) -> None:
    # Правило задачи: эндпоинт под авторизацией, публичным не делать.
    response = await api_client.get("/api/auth/password-policy")

    assert response.status_code == 401


async def test_password_policy_returns_all_codes_with_russian_hints(
    db_session, api_client
) -> None:
    employee = await make_employee(db_session, email="policy-hints@company.local")
    account = await make_account(
        db_session, employee_id=employee.id, auth_type="local", role="employee"
    )

    response = await api_client.get(
        "/api/auth/password-policy", headers=bearer_header(account)
    )

    assert response.status_code == 200
    hints = response.json()
    assert {h["code"] for h in hints} == set(password_policy.PASSWORD_POLICY_VIOLATION_CODES)
    assert all(set(h) == {"code", "hint"} for h in hints)
    # NFR-LOC-001: интерфейс русский — подсказки не заглушки на английском.
    assert all(any(ord(c) > 127 for c in h["hint"]) for h in hints)


async def test_password_policy_is_available_before_first_password_change(
    db_session, api_client
) -> None:
    """PR #143 review, замечание 3: единственное состояние, ради
    которого эндпоинт вообще существует, — учётная запись ещё не
    сменила первичный пароль (`must_change_password = True`) и должна
    увидеть действующие правила ДО того, как сможет их пройти. Эндпоинт
    зависит от `get_current_account_allowing_password_change`, а не от
    гейтящего `get_current_account` (T-M1-46, `rbac.py`) — здесь это
    проверяется прогоном, а не выводится из чтения кода."""
    employee = await make_employee(db_session, email="must-change@company.local")
    account = await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
        must_change_password=True,
    )

    response = await api_client.get(
        "/api/auth/password-policy", headers=bearer_header(account)
    )

    assert response.status_code == 200
    hints = response.json()
    assert {h["code"] for h in hints} == set(password_policy.PASSWORD_POLICY_VIOLATION_CODES)


# --- T-M1-30 scope check --------------------------------------------------


async def test_password_reset_request_endpoint_does_not_exist(api_client) -> None:
    """T-M1-30: восстановление пароля, `FR-AUTH-002`, отложено `T-M1-30`
    — отдельная задача, не эта ветка. Эндпоинт не должен существовать ни
    в каком виде (не 501/200). Критерий не выполняется кодом вовсе — не
    цитируется здесь по номеру намеренно: этот тест фиксирует
    отсутствие, а не закрывает критерий, а идентификатор критерия живёт
    ровно в одном месте — в `_KNOWN_GAPS` проверки покрытия
    (`test_ears_criteria_coverage.py`, `PR #161 review`), иначе
    подстрочный поиск этой же проверки засчитал бы его покрытым."""
    response = await api_client.post(
        "/api/auth/password/reset-request", json={"login": "someone@company.local"}
    )

    assert response.status_code == 404
