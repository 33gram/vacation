"""Обработчики `IntegrityError` и `ImportFileError` в `main.py` (PR
#139 review) — проверены НАПРЯМУЮ, независимо от какого-либо
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
захватил оригинал)."""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.main import (
    _handle_import_file_error,
    _handle_unexpected_integrity_error,
    _handle_weak_password_error,
)
from app.modules.auth.password_policy import (
    VIOLATION_COMMON_PASSWORD,
    VIOLATION_TOO_SHORT,
    PasswordPolicyViolation,
    WeakPasswordError,
)
from app.modules.employees.import_parsing import ImportFileError
from tests.fake_integrity_error import fake_integrity_error


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
