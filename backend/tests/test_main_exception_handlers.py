"""Обработчик `IntegrityError` в `main.py` (PR #139 review, коммит 1) —
проверен НАПРЯМУЮ, независимо от какого-либо конкретного пути записи:
`test_write_paths_safety_net.py` проверяет, что сетка реально
срабатывает через три сегодняшних эндпоинта; этот файл проверяет
поведение самого обработчика (код ответа, безопасность тела и лога) без
привязки к тому, что именно его вызвало.
"""

from __future__ import annotations

import logging

from app.main import _handle_unexpected_integrity_error
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
