"""Ни одна 422 приложения не приносит нерусский текст (`T-M2-28`, `NFR-LOC-001`).

Свип по ВСЕМ маршрутам приложения, принимающим тело (`APIRoute`, POST/PUT/PATCH), обнаружением
из `app.routes`, а не перечнем: новый маршрут с телом попадает под сторожа сам. Каждому
отправляется несколько негодных тел; в ответе 422 у КАЖДОГО элемента `msg` содержит кириллицу.

Аутентификация обходится подменой зависимостей `app.modules.auth.rbac` (роль и токен для этого
свипа не нужны: 422 строится до тела эндпоинта). Маршрут, для которого 422 получить не удалось,
объявляется в `_NO_422_FROM_PROBES` с причиной, иначе тест падает.

Сторож не должен слепнуть: нашёл ли он маршруты и ошибки - проверяется отдельно и громко.

ГРАНИЦЫ (правило 8): проверяется язык `msg` на телах из `_probe_bodies` (неверный тип, пусто,
не объект, слишком длинное, число вне границ, неподходящий формат кода); тип ошибки, который эти
тела не вызывают, этот свип не увидит - его покрывает прямой тест карты
(`test_validation_texts.py`, запасной русский текст для неизвестного `type`). Проверяется
наличие кириллицы, а не грамотность текста.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.modules.auth.rbac as rbac
from app.main import app

_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_BODY_METHODS = {"POST", "PUT", "PATCH"}

# (метод, путь) -> причина, по которой негодное тело 422 не даёт. Пусто: все маршруты с телом
# дали 422 на пробных телах.
_NO_422_FROM_PROBES: dict[tuple[str, str], str] = {}

# Не ниже числа маршрутов с телом на момент написания (15 JSON + 1 multipart): краснеет на
# УДАЛЕНИИ маршрутов и молчит на добавлении - это защита от пустого свипа, а не контроль состава.
_MIN_ROUTES = 15


class _Account:
    id = 1
    role = "system_admin"
    employee_id = 1
    must_change_password = False
    is_active = True
    auth_type = "local"


async def _stub_account() -> _Account:
    return _Account()


@pytest.fixture
def client() -> Iterator[TestClient]:
    overridden = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        stack = list(route.dependant.dependencies)
        while stack:
            dependency = stack.pop()
            stack.extend(dependency.dependencies)
            if getattr(dependency.call, "__module__", "") == rbac.__name__:
                app.dependency_overrides[dependency.call] = _stub_account
                overridden.append(dependency.call)
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        for call in overridden:
            app.dependency_overrides.pop(call, None)


def _body_routes() -> list[tuple[APIRoute, str]]:
    found = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        if route.body_field is None:
            continue
        for method in sorted(route.methods & _BODY_METHODS):
            found.append((route, method))
    return found


def _properties(route: APIRoute) -> list[str]:
    model = route.body_field.type_
    schema = model.model_json_schema() if hasattr(model, "model_json_schema") else {}
    return list(schema.get("properties", {}))


def _probe_bodies(properties: list[str]) -> list[object]:
    return [
        {},
        [],
        {name: {"bad": 1} for name in properties},
        {name: "x" * 5000 for name in properties},
        {name: -1 for name in properties},
        {name: 10**30 for name in properties},
        {name: "NOT-A-VALID-CODE" for name in properties},
    ]


def _path(route: APIRoute, value: str) -> str:
    path = route.path
    for param in route.dependant.path_params:
        path = path.replace("{" + param.name + "}", value)
    return path


def _responses_422(client: TestClient, route: APIRoute, method: str) -> list[list[dict]]:
    responses = []
    is_multipart = getattr(route.body_field.field_info, "media_type", None) == "multipart/form-data"
    for value in ("1", "x"):  # корректный и негодный параметр пути
        path = _path(route, value)
        if is_multipart:
            attempts = [{"data": {"bad": "1"}}]
        else:
            attempts = [{"json": body} for body in _probe_bodies(_properties(route))]
        for kwargs in attempts:
            response = client.request(method, path, **kwargs)
            if response.status_code == 422:
                responses.append(response.json()["detail"])
    return responses


def test_the_sweep_finds_the_routes_with_a_body() -> None:
    assert len(_body_routes()) >= _MIN_ROUTES, "сторож ослеп: маршрутов с телом меньше ожидаемого"


def test_every_422_message_of_every_body_route_is_russian(client: TestClient) -> None:
    total_errors = 0
    silent: list[tuple[str, str]] = []
    english: list[str] = []
    for route, method in _body_routes():
        details = _responses_422(client, route, method)
        if not details and (method, route.path) not in _NO_422_FROM_PROBES:
            silent.append((method, route.path))
        for detail in details:
            for error in detail:
                total_errors += 1
                if not _CYRILLIC.search(error["msg"]):
                    english.append(f"{method} {route.path} type={error['type']}: {error['msg']!r}")

    assert total_errors > 0, "сторож ослеп: ни одной ошибки 422 не получено"
    assert not silent, (
        f"маршрут с телом не дал 422 на пробных телах (объявить в _NO_422_FROM_PROBES): {silent}"
    )
    assert not english, "422 с нерусским текстом:\n" + "\n".join(sorted(set(english)))
