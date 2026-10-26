"""Коды отказов в OpenAPI совпадают с теми, что роутер действительно отдаёт.

Источник «что отдаёт» - тело функции эндпоинта: каждый `HTTPException(status.HTTP_NNN_...)`
в нём (включая `except`-ветки, которые переводят исключения сервиса в коды). Источник
«что объявлено» - `app.openapi()`. Сверяются оба направления: код, который эндпоинт
поднимает, но не объявил, и код, который объявлен, но не поднимается, одинаково врут
клиенту (сгенерированный клиент TS, формы `T-M2-13`/`T-M2-14`).

ГРАНИЦЫ (CLAUDE.md, правило 8) - тест НЕ видит:
  - что сервис действительно умеет поднять исключение, которое `except` переводит в код
    (проверяется только перевод в роутере, а не достижимость ветки);
  - коды из обработчиков уровня приложения (`main.py`: `IntegrityError` -> 409,
    `WeakPasswordError`/`ImportFileError` -> 400) - у четырёх роутеров они совпадают с
    кодами, которые роутер и так объявляет по телу, но сам тест это не доказывает;
  - 422 (его объявляет FastAPI сам по параметрам запроса).
401/403 в теле почти не встречаются: их дают зависимости. Для них проверяется другое -
эндпоинт, в дереве зависимостей которого есть `get_current_account`, обязан объявить оба.
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


def _declared(route: APIRoute) -> set[str]:
    operation = app.openapi()["paths"][route.path][next(iter(route.methods)).lower()]
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
        declared = _declared(route) - _IMPLICIT
        raised = _raised_codes(route) - _IMPLICIT
        if declared != raised:
            problems.append(
                f"{sorted(route.methods)} {route.path}: объявлено {sorted(declared)}, "
                f"в теле поднимается {sorted(raised)}"
            )
    assert not problems, "\n".join(problems)


def test_every_authenticated_endpoint_declares_401_and_403():
    problems = []
    for route in _routes():
        assert _depends_on_current_account(route), route.path
        missing = {"401", "403"} - _declared(route)
        if missing:
            problems.append(f"{sorted(route.methods)} {route.path}: нет {sorted(missing)}")
    assert not problems, "\n".join(problems)


def test_code_raised_in_the_body_but_403_is_declared_too():
    # 403 в теле (`GET /api/employees/{id}`, `GET .../balances`) - тот же код, что дают
    # зависимости; он должен быть объявлен (проверено выше), а не только поднят.
    for route in _routes():
        if "403" in _raised_codes(route):
            assert "403" in _declared(route), route.path
