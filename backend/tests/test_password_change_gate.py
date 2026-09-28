"""Гейт обязательной смены пароля (T-M1-46, FR-AUTH-002 в редакции v1.13,
раздел 13 п. 23) — учётная запись с непогашенным `must_change_password`
не должна пройти ни на один эндпоинт, кроме явного перечня исключений.
Обе половины правила — здесь: «вне перечня отказ всегда» (первый тест) и
«внутри перечня доступ всегда» (остальные) — они должны ломаться в одном
файле и читаться вместе, а не расходиться по модулям.

Проверка идёт ПО ПОВЕДЕНИЮ, а не интроспекцией объектов FastAPI — та же
причина, что и в `test_endpoint_auth_coverage.py` (обход `app.routes`/
`APIRoute` прогонялся и не работает при `include_router`, см. докстринг
там, подтверждено прогоном, не рассуждением). Берём пути и методы из
`app.openapi()["paths"]`, как там же, и для каждого, кроме публичных
(не принимают токен вовсе) и явно исключённых из гейта, ожидаем 403 с
текстом гейта.

Учётная запись в фикстуре — `system_admin`, не `employee`: сегодня отказ
по роли (`require_roles`) и отказ по гейту дают один и тот же код (403),
и с ролью `employee` тест проходил бы на любом эндпоинте, доступном
только `hr_admin`/`system_admin`, независимо от того, работает гейт или
нет. `system_admin` имеет доступ почти everywhere по роли, поэтому 403
на защищённом, не исключённом эндпоинте однозначно означает гейт, а не
ролевую проверку — и сверяется именно ТЕКСТ ответа, а не только код: он
обязан быть текстом гейта («Перед тем как продолжить, необходимо сменить
пароль»), а не «Недостаточно прав для этого действия» — иначе будущая
перестановка порядка проверок внутри `require_roles`/`get_current_account`
молча выхолостила бы этот тест, оставив его зелёным по коду ответа, но
не по причине отказа.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from tests.modules.auth.factories import bearer_header, make_account

# Эндпоинты, не принимающие токен вовсе — им нечего проверять этим тестом.
_PUBLIC_ENDPOINTS: set[tuple[str, str]] = {
    ("post", "/api/auth/login"),
    ("post", "/api/auth/login/local"),
    ("get", "/healthz"),
}

# Исключения из гейта — правка этого списка должна быть видна в дифе
# (T-M1-46). Без первых двух пароль нечем сменить (`GET /api/auth/me`
# отдаёт `must_change_password`, `POST /api/auth/password/change` его
# гасит); без третьего экран смены не может показать действующие
# требования политики; выйти можно всегда.
_EXEMPT_FROM_PASSWORD_CHANGE_GATE: set[tuple[str, str]] = {
    ("get", "/api/auth/me"),
    ("post", "/api/auth/password/change"),
    ("get", "/api/auth/password-policy"),
    ("post", "/api/auth/logout"),
}

_PATH_PARAM_SAMPLES = {"year": "2026", "department_id": "999999", "employee_id": "999999"}

_GATE_MESSAGE = "Перед тем как продолжить, необходимо сменить пароль"


def _concrete_path(path_template: str) -> str:
    path = path_template
    for name, value in _PATH_PARAM_SAMPLES.items():
        path = path.replace(f"{{{name}}}", value)
    assert "{" not in path, f"нет тестового значения для параметра в {path_template!r}"
    return path


def _gated_endpoints() -> list[tuple[str, str]]:
    schema = app.openapi()
    return [
        (method, path)
        for path, methods in schema["paths"].items()
        for method in methods
        if (method, path) not in _PUBLIC_ENDPOINTS
        and (method, path) not in _EXEMPT_FROM_PASSWORD_CHANGE_GATE
    ]


async def test_every_endpoint_outside_the_exemption_list_denies_pending_password_change(
    db_session: AsyncSession, api_client: AsyncClient
) -> None:
    account = await make_account(
        db_session, login="gate-admin", role="system_admin", must_change_password=True
    )
    headers = bearer_header(account)

    endpoints = _gated_endpoints()
    assert endpoints, "OpenAPI-схема приложения не должна быть пустой"

    failures = []
    for method, path_template in endpoints:
        response = await api_client.request(
            method.upper(), _concrete_path(path_template), headers=headers
        )
        if response.status_code != 403:
            failures.append((method.upper(), path_template, response.status_code, response.text))
            continue
        detail = response.json().get("detail")
        if detail != _GATE_MESSAGE:
            failures.append((method.upper(), path_template, response.status_code, detail))

    assert failures == []


# --- Вторая половина: перечень должен пропускать, а не только другие —
# отказывать (T-M1-53 review). Из четырёх исключений
# `_EXEMPT_FROM_PASSWORD_CHANGE_GATE` этим прогоном покрыты только два:
# `POST /api/auth/password/change` (`test_password_change_succeeds`,
# `tests/modules/auth/test_router.py`) и `GET /api/auth/password-policy`
# (`test_password_policy_is_available_before_first_password_change`, тот
# же файл) — оба уже вызываются с `must_change_password=True`, здесь не
# дублируются. `GET /api/auth/me` и `POST /api/auth/logout` — нет: все
# существующие тесты на них используют учётную запись с флагом по
# умолчанию (`False`), поэтому перевод любого из двух на гейтящую
# `get_current_account` (имя отличается от исключающей
# `get_current_account_allowing_password_change` на один довесок) не
# уронил бы ни одного теста — только эти двое ниже.
#
# Последствие такого пробела — не гипотетическое: `SessionGate.tsx`
# (фронтенд) — единственное место, вызывающее `/me`; при ошибке ответа
# оно рисует `ErrorLoadingState` (кнопка «Повторить», не переход
# никуда), а `api/client.ts` обрабатывает глобально только 401, не 403.
# Если бы `/me` попал под гейт, учётная запись с непогашенным
# `must_change_password` осталась бы на этом экране навсегда — кнопка
# «Выйти» живёт в `AppShell`, а он в этом состоянии не рендерится
# (`SessionGate` не пропускает дальше `<Outlet/>`). Первой в это
# упёрлась бы bootstrap-учётная запись администратора при первом
# запуске — ей `must_change_password=True` ставится сразу (`FR-AUTH-005`).


async def test_me_succeeds_with_pending_password_change(
    db_session: AsyncSession, api_client: AsyncClient
) -> None:
    account = await make_account(
        db_session, login="me-before-change", role="employee", must_change_password=True
    )

    response = await api_client.get("/api/auth/me", headers=bearer_header(account))

    assert response.status_code == 200
    # Именно отсюда интерфейс узнаёт, что пароль нужно сменить
    # (`SessionGate.tsx`, `me.must_change_password`) — принципиально
    # проверить не только код ответа, но и это поле.
    assert response.json()["must_change_password"] is True


async def test_logout_succeeds_with_pending_password_change(
    db_session: AsyncSession, api_client: AsyncClient
) -> None:
    account = await make_account(
        db_session, login="logout-before-change", role="employee", must_change_password=True
    )

    response = await api_client.post("/api/auth/logout", headers=bearer_header(account))

    assert response.status_code == 204
