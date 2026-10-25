"""Ни один эндпоинт не остаётся без аутентификации.

`get_current_account` (`app/modules/auth/rbac.py`) — единственная дверь,
но ничто не заставляет будущий роутер её использовать.

Проверка идёт ПО ПОВЕДЕНИЮ, а не интроспекцией объектов FastAPI: обход
`app.routes`/`APIRoute` прогонялся и не работает — при `include_router`
в `app.routes` попадает объект-обёртка, наивный обход не находит ни
одного маршрута и молча рапортует об отсутствии нарушений (проверено
прогоном, не рассуждением — см. `CLAUDE.md`, правило 8). Вместо этого
берём пути и методы из OpenAPI-схемы приложения (`app.openapi()["paths"]`)
и для каждого, кроме явно публичных, бьём запросом без токена — должен
вернуться 401.
"""

from __future__ import annotations

from httpx import AsyncClient

from app.main import app

# Публичные эндпоинты — правка этого списка должна быть видна в диффе.
# `/docs`, `/redoc`, `/openapi.json` в `app.openapi()["paths"]` не
# попадают (это отдельные маршруты, которые FastAPI регистрирует сам, а
# не часть схемы) — перечислены здесь на случай, если это изменится.
_PUBLIC_ENDPOINTS: set[tuple[str, str]] = {
    ("post", "/api/auth/login"),
    ("post", "/api/auth/login/local"),
    ("get", "/healthz"),
    ("get", "/docs"),
    ("get", "/redoc"),
    ("get", "/openapi.json"),
}

# Значения для подстановки в path-параметры (`{year}` и т. п.).
_PATH_PARAM_SAMPLES = {
    "year": "2026",
    "department_id": "999999",
    "employee_id": "999999",
    "leave_type_id": "999999",
}


def _concrete_path(path_template: str) -> str:
    path = path_template
    for name, value in _PATH_PARAM_SAMPLES.items():
        path = path.replace(f"{{{name}}}", value)
    assert "{" not in path, f"нет тестового значения для параметра в {path_template!r}"
    return path


def _protected_endpoints() -> list[tuple[str, str]]:
    schema = app.openapi()
    return [
        (method, path)
        for path, methods in schema["paths"].items()
        for method in methods
        if (method, path) not in _PUBLIC_ENDPOINTS
    ]


async def test_every_endpoint_outside_the_public_allowlist_requires_auth(
    client: AsyncClient,
) -> None:
    endpoints = _protected_endpoints()
    assert endpoints, "OpenAPI-схема приложения не должна быть пустой"

    unauthenticated_successes = []
    for method, path_template in endpoints:
        response = await client.request(method.upper(), _concrete_path(path_template))
        if response.status_code != 401:
            unauthenticated_successes.append(
                (method.upper(), path_template, response.status_code)
            )

    assert unauthenticated_successes == []
