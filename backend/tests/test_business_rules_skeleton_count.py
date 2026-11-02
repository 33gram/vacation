"""Число полей каркаса загрузки раздела «Параметры бизнес-правил» равно числу ключей реестра
(PR #187 check, П-2).

`frontend/src/pages/admin/BusinessRulesPage.tsx` рисует каркас «Загрузки» на
`BUSINESS_RULES_FIELD_COUNT_ESTIMATE` полей (`frontend/src/entities/businessRules.ts`), пока
реестр не пришёл с `GET /api/admin/business-rules`. Число не зависит от данных - `GET` отдаёт
ровно ключи реестра `RULES` (`list_rule_rows`), - поэтому это второе место числа ключей, и без
сверки при новом ключе оно разошлось бы молча. Образец - `test_password_policy_skeleton_count.py`.

Граница (правило 8): проверяется число в исходнике фронтенда по регулярному выражению, а не
отрисовка; в dev-контейнере `frontend/` не смонтирован - тест пропускается, в CI (полный
чекаут) выполняется, как `test_ears_criteria_coverage.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.modules.business_rules.service import RULES

_ENTITY = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "entities"
    / "businessRules.ts"
)

pytestmark = pytest.mark.skipif(
    not _ENTITY.is_file(),
    reason="frontend/ не смонтирован в dev-контейнере; в CI путь резолвится",
)


def _skeleton_fields() -> int:
    source = _ENTITY.read_text("utf-8")
    match = re.search(r"BUSINESS_RULES_FIELD_COUNT_ESTIMATE\s*=\s*(\d+)\s*;", source)
    assert match, "в businessRules.ts нет константы BUSINESS_RULES_FIELD_COUNT_ESTIMATE"
    return int(match.group(1))


def test_the_skeleton_field_count_equals_the_number_of_registry_keys() -> None:
    assert _skeleton_fields() == len(RULES)
