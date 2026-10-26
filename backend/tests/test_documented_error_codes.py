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

АУТЕНТИФИКАЦИЯ фиксируется явно, в обе стороны. Маршрут относится ровно к одной из трёх
групп: открытый без токена (`_PUBLIC_ROUTES`), с токеном без гейта смены пароля
(`_PASSWORD_GATE_EXEMPT_ROUTES`), все остальные - ОБЯЗАНЫ зависеть от `get_current_account`.
Маршрут из списка не должен требовать токен сверх списка, маршрут вне списков - обязан
(иначе потеря зависимости и объявления 401/403 одновременно прошла бы молча).

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
     код (литерал 422 исключён: этот код объявляет FastAPI, в сверку он не входит);
  7. публичность и освобождение от гейта смены пароля ведутся РУКАМИ (`_PUBLIC_ROUTES`,
     `_PASSWORD_GATE_EXEMPT_ROUTES`): новый открытый маршрут без строки в списке краснит
     тест, но лишняя строка про маршрут, который стал закрытым, краснит только по обратной
     сверке (маршрут из списка не должен требовать токен);
  8. маршрут со скрытой схемой (`include_in_schema=False`) в OpenAPI отсутствует: такой
     `APIRoute` не сверяется и обязан быть назван в `_HIDDEN_FROM_SCHEMA` с причиной, иначе
     тест падает с объяснением (сейчас таких нет).
Порог числа маршрутов (`>= 28`) краснеет на УДАЛЕНИИ маршрутов и молчит на добавлении:
это защита от пустого перебора, а не контроль состава.
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

# APIRoute, скрытые из OpenAPI (`include_in_schema=False`): в схеме их нет, сверять не с чем.
_HIDDEN_FROM_SCHEMA: dict[tuple[str, str], str] = {}

# Маршруты, СОЗНАТЕЛЬНО открытые без аутентификации. (метод, путь) -> причина.
_PUBLIC_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/healthz"): "проверка живости для оркестратора, данных не отдаёт",
    ("POST", "/api/auth/login"): "вход: токена у вызывающего ещё нет",
    ("POST", "/api/auth/login/local"): "вход: токена у вызывающего ещё нет",
}

# Маршруты с токеном, но БЕЗ гейта обязательной смены пароля (зависят от
# `get_current_account_allowing_password_change`). Причины - в докстринге этой зависимости.
_PASSWORD_GATE_EXEMPT_ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/auth/password/change"): "иначе первичный пароль нечем сменить",
    ("GET", "/api/auth/password-policy"): "экран смены пароля показывает требования",
    ("POST", "/api/auth/logout"): "выйти можно всегда",
    ("GET", "/api/auth/me"): "флаг must_change_password приходит только отсюда",
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
    routes = [r for r in app.routes if isinstance(r, APIRoute)]
    hidden = {(m, r.path) for r in routes if not r.include_in_schema for m in r.methods}
    assert hidden == set(_HIDDEN_FROM_SCHEMA), (
        "маршрут с include_in_schema=False отсутствует в OpenAPI, сверять его не с чем; "
        "назовите его в _HIDDEN_FROM_SCHEMA с причиной: "
        f"{sorted(hidden ^ set(_HIDDEN_FROM_SCHEMA))}"
    )
    return [r for r in routes if r.include_in_schema]


def _body_codes(route: APIRoute) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(route.endpoint)))
    codes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            match = re.fullmatch(r"HTTP_(\d{3})_[A-Z_]+", node.attr)
            if match and not match.group(1).startswith(_NOT_AN_ERROR_CODE):
                codes.add(match.group(1))
    return codes - _FRAMEWORK_CODES


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
    # Защита от пустого перебора: краснеет на УДАЛЕНИИ маршрутов, на добавлении молчит.
    assert len(_api_routes()) >= 28


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


def test_authentication_is_pinned_in_both_directions():
    problems = []
    for route in _api_routes():
        calls = _dependency_calls(route)
        needs_token = bool(
            calls & {get_current_account, get_current_account_allowing_password_change}
        )
        for method in sorted(route.methods):
            key = (method, route.path)
            if key in _PUBLIC_ROUTES:
                if needs_token:
                    problems.append(f"{key}: в _PUBLIC_ROUTES, но требует токен")
            elif key in _PASSWORD_GATE_EXEMPT_ROUTES:
                if get_current_account_allowing_password_change not in calls:
                    problems.append(f"{key}: в списке без гейта, но зависимость другая")
                if get_current_account in calls:
                    problems.append(f"{key}: в списке без гейта, но гейт применяется")
            elif get_current_account not in calls:
                problems.append(
                    f"{key}: не в списках публичных/без гейта и не зависит от get_current_account"
                )
    assert not problems, "\n".join(problems)


def test_the_authentication_lists_name_only_existing_routes():
    existing = {(m, r.path) for r in _api_routes() for m in r.methods}
    assert set(_PUBLIC_ROUTES) | set(_PASSWORD_GATE_EXEMPT_ROUTES) <= existing
