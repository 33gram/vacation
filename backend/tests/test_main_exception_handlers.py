"""Обработчики `IntegrityError`, `ImportFileError` и
`RequestValidationError` в `main.py` (PR #139 review; `T-M1-40` для
`RequestValidationError`) — проверены НАПРЯМУЮ, независимо от какого-либо
конкретного пути записи: `test_write_paths_safety_net.py` проверяет,
что сетка на `IntegrityError` реально срабатывает через три
сегодняшних эндпоинта; этот файл проверяет поведение самих
обработчиков (код ответа, тело, для `IntegrityError` — безопасность
лога) без привязки к тому, что именно их вызвало.

`_handle_import_file_error` сегодня недостижим через настоящий роутер
импорта — тот ловит `ImportFileError` локально (см. `employee_router.
import_employees_endpoint`), и это нормально: обработчик существует
как сетка на случай пути, который забудет это сделать, как раньше
забыл сам `import_service` при исчерпании повторов записи. Но сетка,
работоспособность которой не показана, сеткой не является (PR #139
review, коммит 2) — `test_import_file_error_handler_saves_a_route_
with_no_local_catch` ниже показывает её на изолированном mini-
приложении с ОДНИМ маршрутом без локального перехвата, той же формы,
что и демонстрация для `IntegrityError` в `test_write_paths_safety_
net.py` (там — временное снятие перехвата в реальном роутере; здесь
это невозможно тем же способом: FastAPI разрешает маршрут на объект
функции в момент регистрации, а не по имени, поэтому патчить функцию
в модуле роутера после `include_router` бесполезно — маршрут уже
захватил оригинал).

`RequestValidationError`, в отличие от трёх обработчиков выше, не может
быть поймана локально каким-либо роутером вообще — её поднимает сам
FastAPI при разборе тела запроса, до вызова кода эндпоинта, так что
обработчик в `main.py` — ЕДИНСТВЕННЫЙ путь построения ответа для нужд
валидации независимо от маршрута. Поэтому здесь нет разделения на
«сетка» и «основной путь», как для `IntegrityError`/`ImportFileError`:
тесты ниже проверяют и сам обработчик напрямую (на синтетической
pydantic-ошибке через mini-приложение — настоящей, не собранной руками,
чтобы видеть реальную форму `exc.errors()`), и восемь конкретных
сценариев через реальные эндпоинты (пять парольных полей, пароль
служебной записи LDAP на двух эндпоинтах, одно поле с персональными
данными) — перечень из тела коммита `T-M1-40`."""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field

from app.main import (
    _handle_import_file_error,
    _handle_request_validation_error,
    _handle_unexpected_integrity_error,
    _handle_weak_password_error,
)
from app.modules.auth.password_policy import (
    MAX_PASSWORD_LENGTH_CHARS,
    VIOLATION_COMMON_PASSWORD,
    VIOLATION_TOO_SHORT,
    PasswordPolicyViolation,
    WeakPasswordError,
)
from app.modules.employees.import_parsing import ImportFileError
from tests.fake_integrity_error import fake_integrity_error
from tests.modules.auth.factories import bearer_header, make_account, make_department, make_employee


class _FakeURL:
    path = "/api/whatever"


class _FakeRequest:
    url = _FakeURL()


async def test_integrity_error_handler_returns_safe_409(caplog) -> None:
    exc = fake_integrity_error("employees_personnel_number_key")

    with caplog.at_level(logging.DEBUG):
        response = await _handle_unexpected_integrity_error(_FakeRequest(), exc)

    assert response.status_code == 409
    assert b"employees_personnel_number_key" not in response.body
    assert response.body  # тело есть и это не пустой 500 по умолчанию


async def test_integrity_error_handler_does_not_log_or_return_exception_text(caplog) -> None:
    # Значение, которое реалистичный DETAIL от asyncpg содержал бы —
    # `str(exc)` не должен попасть НИКУДА, ни в тело ответа, ни в лог.
    secret_value = "Совершенно Секретный Иванов"
    exc = fake_integrity_error("employees_personnel_number_key")
    exc.orig.__cause__.args = (f"DETAIL: Key (full_name)=({secret_value}) already exists.",)

    with caplog.at_level(logging.DEBUG):
        response = await _handle_unexpected_integrity_error(_FakeRequest(), exc)

    assert secret_value.encode() not in response.body
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert secret_value not in log_text


async def test_import_file_error_handler_returns_400_with_message() -> None:
    exc = ImportFileError("File exceeds the 1000 data row limit")

    response = await _handle_import_file_error(_FakeRequest(), exc)

    assert response.status_code == 400
    assert response.body == b'{"detail":"File exceeds the 1000 data row limit"}'


async def test_weak_password_error_handler_returns_400_with_violations() -> None:
    """PR #143 review, коммит 1: сетка уровня приложения, заменившая три
    одинаковых локальных `try/except` (`auth/router.change_password`,
    `employee_router.create_employee`/`update_employee`)."""
    exc = WeakPasswordError(
        [PasswordPolicyViolation(code=VIOLATION_TOO_SHORT, message="Пароль слишком короткий.")]
    )

    response = await _handle_weak_password_error(_FakeRequest(), exc)

    assert response.status_code == 400
    assert json.loads(response.body) == {
        "detail": [{"code": "too_short", "message": "Пароль слишком короткий."}]
    }


async def test_weak_password_error_handler_saves_a_route_with_no_local_catch() -> None:
    """Отрицательный контроль (PR #143 review, коммит 1): изолированное
    mini-приложение с ОДНИМ маршрутом, который поднимает
    `WeakPasswordError` и НЕ ловит её сам — ровно ситуация, для которой
    обработчик в `main.py` и существует, той же формы, что и для
    `ImportFileError` ниже. Убрать регистрацию обработчика здесь — и
    этот тест получит 500 вместо 400, то есть красен именно на сетку,
    а не на какой-то из трёх убранных локальных блоков (их больше нет
    ни в одном роутере)."""
    probe_app = FastAPI()
    probe_app.exception_handler(WeakPasswordError)(_handle_weak_password_error)

    @probe_app.get("/probe")
    async def _probe() -> None:
        raise WeakPasswordError(
            [
                PasswordPolicyViolation(
                    code=VIOLATION_COMMON_PASSWORD, message="Пароль распространён."
                )
            ]
        )

    transport = ASGITransport(app=probe_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/probe")

    assert response.status_code == 400
    assert response.json() == {
        "detail": [{"code": "common_password", "message": "Пароль распространён."}]
    }


async def test_import_file_error_handler_saves_a_route_with_no_local_catch() -> None:
    """PR #139 review, коммит 2: изолированное mini-приложение с ОДНИМ
    маршрутом, который поднимает `ImportFileError` и НЕ ловит её сам —
    ровно ситуация, для которой обработчик в `main.py` и существует.
    Отдельное приложение (не `app.main.app`), чтобы не регистрировать
    лишний маршрут в реальной схеме — это сломало бы `test_endpoint_
    auth_coverage.py`, которая перебирает `app.openapi()["paths"]`."""
    probe_app = FastAPI()
    probe_app.exception_handler(ImportFileError)(_handle_import_file_error)

    @probe_app.get("/probe")
    async def _probe() -> None:
        raise ImportFileError("File exceeds the 1000 data row limit")

    transport = ASGITransport(app=probe_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/probe")

    assert response.status_code == 400
    assert response.json() == {"detail": "File exceeds the 1000 data row limit"}


# --- RequestValidationError (T-M1-40, NFR-SEC-004/NFR-SEC-001/NFR-SEC-002) --

# Заведомо узнаваемый, не совпадающий ни с чем служебным (не похож ни на
# реальный пароль, ни на текст ошибки, ни на что-либо, что сервер мог бы
# сгенерировать сам).
_PASSWORD_MARKER = "MARKER-T-M1-40-PASSWORD-LEAK-" + "x" * MAX_PASSWORD_LENGTH_CHARS
_LDAP_PASSWORD_MARKER = "MARKER-T-M1-40-LDAP-BIND-PASSWORD-LEAK"
_PII_MARKER = "MARKER-T-M1-40-PII-LEAK"


class _ProbeBody(BaseModel):
    # На уровне модуля, не внутри теста: при `from __future__ import
    # annotations` (см. верх файла) FastAPI резолвит строковую аннотацию
    # параметра маршрута через `typing.get_type_hints()`, которому не
    # виден класс, объявленный в локальной области видимости функции —
    # маршрут тогда молча принимал параметр за query, а не body
    # (проверено прогоном: `loc` был `["query", "payload"]`).
    secret: str = Field(max_length=5)


async def test_request_validation_error_handler_strips_value_keeps_diagnostics() -> None:
    """Мини-приложение с НАСТОЯЩЕЙ pydantic-ошибкой (`max_length`), не
    собранной руками, — та же форма `exc.errors()`, что видна в реальных
    запросах (см. восемь сценариев через реальные эндпоинты ниже).
    Отдельное приложение по той же причине, что и для `ImportFileError`
    выше: не регистрировать лишний маршрут в `app.main.app`."""
    probe_app = FastAPI()
    probe_app.exception_handler(RequestValidationError)(_handle_request_validation_error)

    @probe_app.post("/probe")
    async def _probe(payload: _ProbeBody) -> None:
        return None

    transport = ASGITransport(app=probe_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/probe", json={"secret": _PASSWORD_MARKER})

    assert response.status_code == 422
    assert _PASSWORD_MARKER not in response.text
    [error] = response.json()["detail"]
    # Белый список `loc`/`msg`/`type` — ровно эти три ключа, не больше и
    # не меньше: лишний ключ сверх белого списка означает дыру в фильтре,
    # отсутствие одного из трёх — потерю диагностики (см. докстринг
    # обработчика).
    assert set(error.keys()) == {"loc", "msg", "type"}
    assert error["loc"] == ["body", "secret"]


async def _make_system_admin(db_session):
    employee = await make_employee(db_session, email=f"admin{id(object())}@company.local")
    return await make_account(
        db_session, employee_id=employee.id, auth_type="local", role="system_admin"
    )


# --- Пять парольных полей ---------------------------------------------------


async def test_login_password_too_long_does_not_leak_it(api_client) -> None:
    response = await api_client.post(
        "/api/auth/login",
        json={"login": "someone@company.local", "password": _PASSWORD_MARKER},
    )

    assert response.status_code == 422
    assert _PASSWORD_MARKER not in response.text


async def test_password_change_current_password_too_long_does_not_leak_it(
    db_session, api_client
) -> None:
    account = await _make_system_admin(db_session)

    response = await api_client.post(
        "/api/auth/password/change",
        json={"current_password": _PASSWORD_MARKER, "new_password": "Zqx9!Trvb"},
        headers=bearer_header(account),
    )

    assert response.status_code == 422
    assert _PASSWORD_MARKER not in response.text


async def test_password_change_new_password_too_long_does_not_leak_it(
    db_session, api_client
) -> None:
    account = await _make_system_admin(db_session)

    response = await api_client.post(
        "/api/auth/password/change",
        json={"current_password": "Zqx9!Trvb", "new_password": _PASSWORD_MARKER},
        headers=bearer_header(account),
    )

    assert response.status_code == 422
    assert _PASSWORD_MARKER not in response.text


async def test_create_employee_initial_password_too_long_does_not_leak_it(
    db_session, api_client
) -> None:
    department = await make_department(db_session)
    account = await _make_system_admin(db_session)

    response = await api_client.post(
        "/api/employees",
        json={
            "full_name": "Тестовый Сотрудник",
            "personnel_number": "TM140-1",
            "department_id": department.id,
            "position": "Инженер",
            "hire_date": "2024-01-15",
            "email": "tm140-1@company.local",
            "work_schedule_type": "standard",
            "auth_type": "local",
            "initial_password": _PASSWORD_MARKER,
        },
        headers=bearer_header(account),
    )

    assert response.status_code == 422
    assert _PASSWORD_MARKER not in response.text


async def test_update_employee_new_password_too_long_does_not_leak_it(
    db_session, api_client
) -> None:
    department = await make_department(db_session)
    employee = await make_employee(db_session, email="tm140-2@company.local")
    await make_account(db_session, employee_id=employee.id, role="employee", auth_type="ldap")
    account = await _make_system_admin(db_session)

    response = await api_client.put(
        f"/api/employees/{employee.id}",
        json={
            "full_name": employee.full_name,
            "personnel_number": employee.personnel_number,
            "department_id": department.id,
            "position": "Инженер",
            "hire_date": "2024-01-15",
            "email": employee.email,
            "work_schedule_type": "standard",
            "auth_type": "local",
            "new_password": _PASSWORD_MARKER,
        },
        headers=bearer_header(account),
    )

    assert response.status_code == 422
    assert _PASSWORD_MARKER not in response.text


# --- Пароль служебной записи LDAP (нет max_length — утечка не привязана
# к этому ограничению) ------------------------------------------------------


async def test_put_ldap_settings_non_string_bind_password_does_not_leak_it(
    db_session, api_client
) -> None:
    account = await _make_system_admin(db_session)

    response = await api_client.put(
        "/api/admin/ldap-settings",
        json={
            "server_url": "ldap.company.local",
            "port": 389,
            "base_dn": "dc=company,dc=local",
            "bind_dn": "cn=svc,dc=company,dc=local",
            "bind_password": {"marker": _LDAP_PASSWORD_MARKER},
            "user_match_attribute": "mail",
            "is_active": False,
        },
        headers=bearer_header(account),
    )

    assert response.status_code == 422
    assert _LDAP_PASSWORD_MARKER not in response.text


async def test_test_ldap_connection_non_string_bind_password_does_not_leak_it(
    db_session, api_client
) -> None:
    account = await _make_system_admin(db_session)

    response = await api_client.post(
        "/api/admin/ldap-settings/test",
        json={"bind_password": [_LDAP_PASSWORD_MARKER]},
        headers=bearer_header(account),
    )

    assert response.status_code == 422
    assert _LDAP_PASSWORD_MARKER not in response.text


# --- Непарольное поле с персональными данными (NFR-SEC-001/NFR-SEC-002) -----


async def test_create_employee_wrong_type_email_does_not_leak_it(db_session, api_client) -> None:
    department = await make_department(db_session)
    account = await _make_system_admin(db_session)

    response = await api_client.post(
        "/api/employees",
        json={
            "full_name": "Тестовый Сотрудник",
            "personnel_number": "TM140-3",
            "department_id": department.id,
            "position": "Инженер",
            "hire_date": "2024-01-15",
            "email": {"marker": _PII_MARKER},
            "work_schedule_type": "standard",
        },
        headers=bearer_header(account),
    )

    assert response.status_code == 422
    assert _PII_MARKER not in response.text
    # Диагностика сохранена: по `loc` по-прежнему видно, какое поле не
    # прошло, иначе чистка сделала бы отказ непонятным.
    [error] = response.json()["detail"]
    assert set(error.keys()) == {"loc", "msg", "type"}
    assert error["loc"] == ["body", "email"]
