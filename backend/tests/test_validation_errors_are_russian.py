"""Ни одна 422 приложения не приносит нерусский текст (`T-M2-28`, `NFR-LOC-001`).

Свип по ВСЕМ маршрутам приложения, принимающим тело (`APIRoute`, POST/PUT/PATCH), обнаружением
из `app.routes`, а не перечнем: новый маршрут с телом попадает под сторожа сам. Каждому
отправляется несколько негодных тел; в ответе 422 у КАЖДОГО элемента `msg` содержит кириллицу.

Аутентификация обходится подменой зависимостей `app.modules.auth.rbac` (роль и токен для этого
свипа не нужны). Тело эндпоинта при этом НЕ выполняется, и это обеспечивает не общее свойство
FastAPI, а проверка в самом свипе: каждое пробное тело перед отправкой прогоняется МОДЕЛЬЮ тела
этого маршрута (`route.body_field.validate`), принимаемое отсеивается (`_invalid_bodies`), а при
отправке то же утверждается ещё раз (`_assert_rejected`) - иначе эндпоинт с необязательными
полями (`LdapTestRequest`: `{}` валидно) выполнился бы, пошёл в LDAP и записал результат
проверки. Маршрут, у которого после отсева не осталось ни одного негодного тела, или чей 422
получить не удалось, объявляется в `_NO_422_FROM_PROBES` с причиной, иначе тест падает.

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


def _probe_bodies(properties: list[str]) -> list[object]:  # все варианты, до отсева
    return [
        {},
        [],
        {name: {"bad": 1} for name in properties},
        {name: "x" * 5000 for name in properties},
        {name: -1 for name in properties},
        {name: 10**30 for name in properties},
        {name: "NOT-A-VALID-CODE" for name in properties},
    ]


def _accepted_by_the_model(route: APIRoute, body: object) -> bool:
    """Тело, которое модель тела маршрута принимает: такое эндпоинт выполнил бы."""
    _, errors = route.body_field.validate(body, {}, loc=("body",))
    return not errors


def _invalid_bodies(route: APIRoute) -> list[object]:
    probes = _probe_bodies(_properties(route))
    return [body for body in probes if not _accepted_by_the_model(route, body)]


def _assert_rejected(route: APIRoute, body: object) -> None:
    assert not _accepted_by_the_model(route, body), (
        f"пробное тело {body!r} модель маршрута {route.path} принимает: эндпоинт выполнился бы"
    )


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
            attempts = [{"json": body} for body in _invalid_bodies(route)]
        for kwargs in attempts:
            if "json" in kwargs:
                _assert_rejected(route, kwargs["json"])
            response = client.request(method, path, **kwargs)
            if response.status_code == 422:
                responses.append(response.json()["detail"])
    return responses


def test_the_sweep_finds_the_routes_with_a_body() -> None:
    assert len(_body_routes()) >= _MIN_ROUTES, "сторож ослеп: маршрутов с телом меньше ожидаемого"


def test_a_body_the_model_accepts_is_never_sent() -> None:
    # `LdapTestRequest`: все поля необязательны, `{}` валидно и означает «проверить сохранённое»
    # (эндпоинт ходит в LDAP и пишет результат проверки) - такое тело сюда отправляться не должно.
    [route] = [r for r, _ in _body_routes() if r.path == "/api/admin/ldap-settings/test"]

    assert _accepted_by_the_model(route, {})
    assert {} not in _invalid_bodies(route)
    assert _invalid_bodies(route), "после отсева у маршрута не осталось негодных тел"


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
