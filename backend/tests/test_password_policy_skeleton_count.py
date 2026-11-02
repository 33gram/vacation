"""Число строк каркаса загрузки чек-листа пароля (PR #185 review, п. 3) равно числу правил.

`frontend/src/components/PasswordPolicyChecklist.tsx` рисует каркас «Загрузки» на
`PASSWORD_POLICY_HINTS_ESTIMATE` строк, пока правила не пришли с сервера
(`GET /api/auth/password-policy`). Это второе место числа правил; без сверки при новом правиле
политики оно разошлось бы молча. Сверка идёт по обоим перечням: кодам нарушений
(`PASSWORD_POLICY_VIOLATION_CODES`) и подсказкам, которые эндпоинт действительно отдаёт, - по
смыслу они равны (каждый код имеет ровно одну подсказку), и тест падает, если когда-нибудь
перестанут.

Граница (правило 8): проверяется число в исходнике фронтенда по регулярному выражению, а не
поведение отрисовки; compose не монтирует `frontend/src` в dev-контейнер `backend`
(монтируются `backend/app`, `backend/alembic`, `backend/tests`, `backend/openapi.json`) - тест
пропускается, пока каталог не скопирован туда вручную (`docker cp`), в CI (полный чекаут)
выполняется, как `test_ears_criteria_coverage.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.modules.auth.password_policy import (
    PASSWORD_POLICY_VIOLATION_CODES,
    get_password_policy_hints,
)

_COMPONENT = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "components"
    / "PasswordPolicyChecklist.tsx"
)

pytestmark = pytest.mark.skipif(
    not _COMPONENT.is_file(),
    reason=(
        "frontend/src не смонтирован compose-файлом в dev-контейнер backend "
        "(только backend/app, alembic, tests, openapi.json) и есть там, только если "
        "скопирован вручную (docker cp); в CI путь резолвится"
    ),
)


def _skeleton_rows() -> int:
    source = _COMPONENT.read_text("utf-8")
    match = re.search(r"PASSWORD_POLICY_HINTS_ESTIMATE\s*=\s*(\d+)\s*;", source)
    assert match, "в PasswordPolicyChecklist.tsx нет константы PASSWORD_POLICY_HINTS_ESTIMATE"
    return int(match.group(1))


def test_the_skeleton_row_count_equals_the_number_of_password_policy_codes() -> None:
    assert _skeleton_rows() == len(PASSWORD_POLICY_VIOLATION_CODES)


def test_every_policy_code_is_shown_so_codes_and_displayed_rules_are_the_same_number() -> None:
    hints = get_password_policy_hints(min_length=8)

    assert {hint.code for hint in hints} == set(PASSWORD_POLICY_VIOLATION_CODES)
    assert _skeleton_rows() == len(hints)
