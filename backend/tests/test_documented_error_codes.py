"""Коды отказов в OpenAPI сверяются с литералами `status.HTTP_NNN_*` в теле эндпоинта.

Что сверяется: множество `status.HTTP_NNN_*` (кроме 2xx), встретившихся в исходнике
функции эндпоинта (`inspect.getsource`), против кодов отказа этого маршрута в
`app.openapi()`, в обе стороны. Маршрут с несколькими методами проверяется по КАЖДОМУ
методу (в `app.openapi()` у каждого свой набор). Отдельно: эндпоинт, у которого в
зависимостях есть `get_current_account`, обязан объявить 401 и 403.

ГРАНИЦЫ (CLAUDE.md, правило 8) - сторож НЕ видит:
  1. код, записанный не как `status.HTTP_NNN_*`: `HTTPException(404, ...)`,
     `status_code=404` литералом, код из словаря или константы;
  2. `raise` в хелпере или вложенной функции - вне исходника самого эндпоинта;
  3. коды от зависимостей, кроме 401/403 (для них - только проверка выше, и она требует
     наличия кода в схеме, а не доказывает, что зависимость его отдаёт);
  4. коды обработчиков уровня приложения (`main.py`: `IntegrityError` -> 409,
     `WeakPasswordError`/`ImportFileError` -> 400) и 422 (его объявляет FastAPI сам);
  5. достижимость: сервис мог и не уметь поднять исключение, которое `except` переводит
     в код - проверяется перевод в роутере, а не ветка;
  6. ложные срабатывания в другую сторону: ЛЮБОЕ упоминание `status.HTTP_4xx` в теле
     (в сравнении, в комментарии-коде, в ветке без `raise`) засчитывается как поднимаемый
     код.
Охват задан `_COVERED_PREFIXES`: новый ЭНДПОИНТ в перечисленном префиксе подхватывается
сам, новый ПРЕФИКС (роутер) - нет, его нужно добавить сюда руками. Роутеры вне списка
не сверяются вовсе (`T-M2-22`).
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap

from fastapi.routing import APIRoute

from app.main import app
from app.modules.auth.rbac import get_current_account

# Роутеры, охваченные сверкой (PR #182 check). Остальные - `T-M2-22`.
_COVERED_PREFIXES = ("/api/employees", "/api/departments", "/api/leave-types")
_IMPLICIT = {"401", "403", "422"}


def _routes() -> list[APIRoute]:
    return [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and r.path.startswith(_COVERED_PREFIXES)
    ]


def _raised_codes(route: APIRoute) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(route.endpoint)))
    codes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            match = re.fullmatch(r"HTTP_(\d{3})_[A-Z_]+", node.attr)
            if match and not match.group(1).startswith("2"):  # 2xx - не отказы
                codes.add(match.group(1))
    return codes


def _depends_on_current_account(route: APIRoute) -> bool:
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        if dep.call is get_current_account:
            return True
        stack.extend(dep.dependencies)
    return False


def _declared(route: APIRoute, method: str) -> set[str]:
    operation = app.openapi()["paths"][route.path][method.lower()]
    return {
        code
        for code in operation["responses"]
        if code.isdigit() and not code.startswith("2")
    }


def test_covered_routes_are_found():
    # Защита от пустого перебора: переименование префикса не должно молча отключить сверку.
    assert len(_routes()) >= 14


def test_declared_error_codes_match_the_codes_the_endpoint_raises():
    problems = []
    for route in _routes():
        raised = _raised_codes(route) - _IMPLICIT
        for method in sorted(route.methods):
            declared = _declared(route, method) - _IMPLICIT
            if declared != raised:
                problems.append(
                    f"{method} {route.path}: объявлено {sorted(declared)}, "
                    f"в теле поднимается {sorted(raised)}"
                )
    assert not problems, "\n".join(problems)


def test_every_authenticated_endpoint_declares_401_and_403():
    problems = []
    for route in _routes():
        assert _depends_on_current_account(route), route.path
        for method in sorted(route.methods):
            missing = {"401", "403"} - _declared(route, method)
            if missing:
                problems.append(f"{method} {route.path}: нет {sorted(missing)}")
    assert not problems, "\n".join(problems)


def test_code_raised_in_the_body_but_403_is_declared_too():
    # 403 в теле (`GET /api/employees/{id}`, `GET .../balances`) - тот же код, что дают
    # зависимости; он должен быть объявлен (проверено выше), а не только поднят.
    for route in _routes():
        if "403" in _raised_codes(route):
            for method in route.methods:
                assert "403" in _declared(route, method), route.path
