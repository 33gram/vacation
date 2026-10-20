"""Сторож соответствия: таблица атрибутов раздела 6 `docs/requirements.md`
против колонок `leave_types` (T-M2-01).

Перечень атрибутов берётся ИЗ ДОКУМЕНТА, а не из этого файла: атрибут,
добавленный в раздел 6, без записи в `_ATTRIBUTE_COLUMNS` краснит
сторожа, а не теряется. Явный здесь только словарь «атрибут → колонки» —
правка видна в дифе, по образцу `_KNOWN_GAPS`
(`test_ears_criteria_coverage.py`). Путь к `docs/` резолвится тем же
способом и по той же причине, что там: в dev-контейнере смонтирован
только `backend/`, в CI — весь чекаут.

Граница (правило 8): сторож подтверждает, что КОЛОНКА с названным именем
существует, а не что она выражает атрибут верно (тип, набор значений,
полярность) — это забота тестов ограничений и ревью.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.models.leave_type import LeaveType

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REQUIREMENTS_PATH = _REPO_ROOT / "docs" / "requirements.md"

pytestmark = pytest.mark.skipif(
    not _REQUIREMENTS_PATH.is_file(),
    reason=(
        "docs/requirements.md не резолвится по вычисленному пути "
        "(parents[2] от backend/tests) - та же причина, что у "
        "test_ears_criteria_coverage.py: внутри dev-контейнера backend "
        "смонтировано только поддерево backend/; в CI путь резолвится верно"
    ),
)

# Атрибут раздела 6 (первая ячейка строки таблицы, дословно) -> колонки.
_ATTRIBUTE_COLUMNS: dict[str, tuple[str, ...]] = {
    "Код и наименование": ("code", "name"),
    "Оплачиваемость": ("is_paid",),
    "Единица учёта": ("unit",),
    "Уменьшает остаток основного отпуска": ("reduces_main_balance",),
    "Требует подтверждающий документ": ("requires_document",),
    # Маршрут («и по какому маршруту») здесь не хранится — это
    # `approval_routes.leave_type_id` (`plan.md` §4, T-M3-04).
    "Требует согласования": ("requires_approval",),
    "Годовой лимит": ("annual_limit", "carry_over_policy"),
    "Учитывать выходные/праздники внутри периода": ("counted_non_working_days",),
}

# Колонки, которым НЕ соответствует строка раздела 6: ключ и признак
# активности (A-3.1, `plan.md` §4).
_NON_ATTRIBUTE_COLUMNS = {"id", "is_active"}


def _section6_attributes(requirements_text: str) -> list[str]:
    section = re.search(
        r"^## 6\. Справочник видов отсутствий\n(.*?)(?=^## )", requirements_text, re.M | re.S
    )
    assert section, "в requirements.md не найден раздел 6"
    table_rows = [
        line for line in section.group(1).splitlines() if line.startswith("|")
    ]
    # Шапка и разделитель — первые две строки таблицы.
    attributes = [row.split("|")[1].strip() for row in table_rows[2:]]
    assert attributes, "в разделе 6 не найдена таблица атрибутов"
    return attributes


def find_gaps(requirements_text: str, columns: set[str]) -> list[str]:
    attributes = _section6_attributes(requirements_text)
    problems = []
    for attribute in attributes:
        mapped = _ATTRIBUTE_COLUMNS.get(attribute)
        if mapped is None:
            problems.append(f"атрибут раздела 6 без отображения в колонки: {attribute!r}")
            continue
        problems += [
            f"атрибут {attribute!r}: колонки {column!r} нет в leave_types"
            for column in mapped
            if column not in columns
        ]
    problems += [
        f"отображение для атрибута, которого нет в разделе 6: {attribute!r}"
        for attribute in _ATTRIBUTE_COLUMNS
        if attribute not in attributes
    ]
    mapped_columns = {c for cols in _ATTRIBUTE_COLUMNS.values() for c in cols}
    problems += [
        f"колонка {column!r} не соответствует ни одному атрибуту раздела 6"
        for column in sorted(columns - mapped_columns - _NON_ATTRIBUTE_COLUMNS)
    ]
    return problems


_COLUMNS = {column.name for column in LeaveType.__table__.columns}


def test_every_section6_attribute_is_a_leave_types_column() -> None:
    text = _REQUIREMENTS_PATH.read_text(encoding="utf-8")

    assert find_gaps(text, _COLUMNS) == []


def test_attribute_added_to_section6_is_reported() -> None:
    text = _REQUIREMENTS_PATH.read_text(encoding="utf-8")
    anchor = "| Учитывать выходные/праздники внутри периода |"
    assert anchor in text
    extended = text.replace(anchor, "| Новый атрибут |  Описание. |\n" + anchor, 1)

    assert find_gaps(extended, _COLUMNS) == [
        "атрибут раздела 6 без отображения в колонки: 'Новый атрибут'"
    ]


def test_missing_column_is_reported() -> None:
    text = _REQUIREMENTS_PATH.read_text(encoding="utf-8")

    problems = find_gaps(text, _COLUMNS - {"unit"})

    assert problems == ["атрибут 'Единица учёта': колонки 'unit' нет в leave_types"]


def test_unmapped_column_is_reported() -> None:
    text = _REQUIREMENTS_PATH.read_text(encoding="utf-8")

    problems = find_gaps(text, _COLUMNS | {"surprise"})

    assert problems == ["колонка 'surprise' не соответствует ни одному атрибуту раздела 6"]
