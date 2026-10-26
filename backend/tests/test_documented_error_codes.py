"""Коды отказов в OpenAPI сверяются с тем, что отдаёт каждый маршрут приложения.

Охват - ВСЕ маршруты `app.routes`, без перечня префиксов «что проверяем». Исключения
заданы наоборот - явными списками «что сознательно не проверяем» с причиной у каждого
(`_NOT_API_ROUTES`), поэтому новый роутер (в том числе новый префикс) попадает под сторож
сам, а новый маршрут не из `APIRoute` проваливает тест и требует решения.

Что сверяется: объявленные в `app.openapi()` коды отказа маршрута (кроме 2xx и 422, который
объявляет FastAPI сам) против объединения трёх источников «что маршрут отдаёт»:
  1. литералы `status.HTTP_NNN_*` в исходнике функции эндпоинта (`inspect.getsource`);
  2. коды зависимостей, выведенные из дерева `route.dependant`: `get_current_account` даёт
     401 и 403 (токен; гейт смены пароля; роль через `require_roles`),
     `get_current_account_allowing_password_change` - только 401;
  3. таблица `_CODES_FROM_APP_HANDLERS` - коды, которые в теле не видны, потому что их строят
     обработчики `main.py`; ЗАПОЛНЯЕТСЯ ВРУЧНУЮ, с причиной у каждой строки.
Маршрут с несколькими методами проверяется по КАЖДОМУ методу.

ГРАНИЦЫ (CLAUDE.md, правило 8) - сторож НЕ видит:
  1. код, записанный не как `status.HTTP_NNN_*`: `HTTPException(404, ...)`,
     `status_code=404` литералом, код из словаря или константы;
  2. `raise` в хелпере или вложенной функции - вне исходника самого эндпоинта;
  3. коды зависимостей, кроме тех, что выводятся из двух функций выше (другая зависимость,
     поднимающая своё исключение, останется незамеченной);
  4. коды обработчиков уровня приложения вне таблицы `_CODES_FROM_APP_HANDLERS`: она
     пополняется руками, и забытая строка даёт ложную тревогу в безопасную сторону (объявленный
     код без источника), а вот лишний код в таблице скроет расхождение;
  5. достижимость: сервис мог и не уметь поднять исключение, которое `except` переводит
     в код - проверяется перевод в роутере, а не ветка;
  6. ложные срабатывания в другую сторону: ЛЮБОЕ упоминание `status.HTTP_4xx` в теле
     (в сравнении, в комментарии-коде, в ветке без `raise`) засчитывается как поднимаемый
     код.
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap

from fastapi.routing import APIRoute

from app.main import app
from app.modules.auth.rbac import (
    get_current_account,
    get_current_account_allowing_password_change,
)

_NOT_AN_ERROR_CODE = ("2",)  # 2xx - успех
_FRAMEWORK_CODES = {"422"}  # объявляет сам FastAPI по параметрам запроса

# Маршруты приложения, не являющиеся `APIRoute` и потому отсутствующие в OpenAPI. Каждый -
# служебная страница самого FastAPI; новый такой маршрут не пройдёт `test_every_route_is_...`
# без строки здесь.
_NOT_API_ROUTES: dict[str, str] = {
    "/openapi.json": "сама схема, в схему не входит (include_in_schema=False)",
    "/docs": "Swagger UI, служебная страница FastAPI",
    "/docs/oauth2-redirect": "служебный редирект Swagger UI",
    "/redoc": "ReDoc, служебная страница FastAPI",
}

# Коды, которые маршрут отдаёт через обработчики `main.py` (в теле эндпоинта их не видно).
# (метод, путь) -> (коды, причина).
_CODES_FROM_APP_HANDLERS: dict[tuple[str, str], tuple[set[str], str]] = {
    ("POST", "/api/auth/login"): (
        {"401", "403"},
        "AuthenticationError -> 401, AccessDeniedError -> 403 (LDAP-вход): обработчики main.py",
    ),
    ("POST", "/api/auth/login/local"): (
        {"401"},
        "AuthenticationError -> 401: обработчик main.py; local_service 403 не поднимает",
    ),
}


def _api_routes() -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute)]


def _body_codes(route: APIRoute) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(route.endpoint)))
    codes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            match = re.fullmatch(r"HTTP_(\d{3})_[A-Z_]+", node.attr)
            if match and not match.group(1).startswith(_NOT_AN_ERROR_CODE):
                codes.add(match.group(1))
    return codes


def _dependency_calls(route: APIRoute) -> set:
    calls = set()
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        calls.add(dep.call)
        stack.extend(dep.dependencies)
    return calls


def _dependency_codes(route: APIRoute) -> set[str]:
    calls = _dependency_calls(route)
    if get_current_account in calls:
        return {"401", "403"}
    if get_current_account_allowing_password_change in calls:
        return {"401"}
    return set()


def _expected(route: APIRoute, method: str) -> set[str]:
    from_handlers = _CODES_FROM_APP_HANDLERS.get((method, route.path), (set(), ""))[0]
    return _body_codes(route) | _dependency_codes(route) | from_handlers


def _declared(route: APIRoute, method: str) -> set[str]:
    operation = app.openapi()["paths"][route.path][method.lower()]
    return {
        code
        for code in operation["responses"]
        if code.isdigit()
        and not code.startswith(_NOT_AN_ERROR_CODE)
        and code not in _FRAMEWORK_CODES
    }


def test_every_route_is_checked_or_explicitly_excluded():
    # Новый маршрут не из `APIRoute` (Mount, Route) должен получить решение, а не пройти молча.
    others = {r.path for r in app.routes if not isinstance(r, APIRoute)}
    assert others == set(_NOT_API_ROUTES), sorted(others ^ set(_NOT_API_ROUTES))
    assert len(_api_routes()) >= 28  # защита от пустого перебора


def test_the_handler_table_names_only_existing_routes():
    existing = {(m, r.path) for r in _api_routes() for m in r.methods}
    assert set(_CODES_FROM_APP_HANDLERS) <= existing


def test_declared_error_codes_match_what_each_route_returns():
    problems = []
    for route in _api_routes():
        for method in sorted(route.methods):
            declared = _declared(route, method)
            expected = _expected(route, method)
            if declared != expected:
                problems.append(
                    f"{method} {route.path}: объявлено {sorted(declared)}, "
                    f"маршрут отдаёт {sorted(expected)} "
                    f"(тело {sorted(_body_codes(route))}, "
                    f"зависимости {sorted(_dependency_codes(route))})"
                )
    assert not problems, "\n".join(problems)
