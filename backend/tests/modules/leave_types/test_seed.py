"""Предустановленный набор видов отсутствий (T-M2-04, раздел 6, A-3.1).

Закрывает ровно то, что закрывает T-M2-01/T-M2-04: схема выражает
атрибуты раздела 6 и набор предустановлен. Управление видами —
интерфейс и эндпоинты `FR-ADMIN-001` — задача T-M2-10, здесь не
проверяется.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import text

_MIGRATION = Path(__file__).resolve().parents[3] / "alembic" / "versions" / "0006_leave_types.py"

# Независимый от миграции перечень: если код в миграции изменится, тест
# покраснеет — коды часть контракта.
_EXPECTED_CODES = {
    "annual_paid",
    "additional_paid",
    "unpaid",
    "study",
    "maternity_childcare",
    "sick",
    "time_off",
    "business_trip",
}


def _load_seed():
    spec = importlib.util.spec_from_file_location("migration_0006", _MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.seed_preset


async def _rows(db_session) -> dict[str, dict]:
    result = await db_session.execute(text("SELECT * FROM leave_types"))
    return {row["code"]: dict(row) for row in result.mappings()}


async def _run_seed(db_session) -> None:
    seed = _load_seed()
    await db_session.run_sync(lambda session: seed(session.connection()))


async def test_exactly_the_eight_preset_types_exist(db_session) -> None:
    rows = await _rows(db_session)

    assert set(rows) == _EXPECTED_CODES


async def test_additional_paid_leave_is_inactive_and_the_rest_are_active(db_session) -> None:
    rows = await _rows(db_session)

    assert rows["additional_paid"]["is_active"] is False
    assert {code for code, row in rows.items() if not row["is_active"]} == {"additional_paid"}


# Ожидаемое содержимое справочника после миграции — ВСЕ атрибуты всех
# восьми видов, записанные здесь независимо от миграции. Любое изменение
# сидирования обязано покраснеть: пометка ВЫВЕДЕНО/ВЫБРАНО в миграции
# объясняет происхождение значения, а не степень его защищённости.
# Источники самых весомых значений: FR-SICK-001 (больничный не уменьшает
# остаток, нужен ЭЛН), раздел 6 п. 8 (командировка не расходует остаток),
# BR-9 (без сохранения зарплаты), BR-1 (основной отпуск — календарные
# дни, уменьшает основной остаток), ст. 120 ТК РФ (`weekends_only`),
# A-3.1/T-M2-04 (дополнительный отпуск неактивен), правило 5 и T-M2-03
# (`annual_limit` пуст — нормы закона живут в `business_rules_config`).
_EXPECTED = {
    "annual_paid": (
        "Основной оплачиваемый отпуск",
        "full",
        "calendar_days",
        True,
        False,
        True,
        None,
        "unlimited",
        "weekends_only",
        True,
    ),
    "additional_paid": (
        "Дополнительный оплачиваемый отпуск",
        "full",
        "calendar_days",
        False,
        False,
        True,
        None,
        "none",
        "weekends_only",
        False,
    ),
    "unpaid": (
        "Отпуск без сохранения заработной платы",
        "none",
        "calendar_days",
        False,
        False,
        True,
        None,
        "none",
        "all",
        True,
    ),
    "study": (
        "Учебный отпуск",
        "full",
        "calendar_days",
        False,
        True,
        True,
        None,
        "none",
        "all",
        True,
    ),
    "maternity_childcare": (
        "Отпуск по беременности и родам / по уходу за ребёнком",
        "partial",
        "calendar_days",
        False,
        True,
        True,
        None,
        "none",
        "all",
        True,
    ),
    "sick": (
        "Больничный (временная нетрудоспособность)",
        "partial",
        "calendar_days",
        False,
        True,
        True,
        None,
        "none",
        "all",
        True,
    ),
    "time_off": ("Отгул", "full", "working_days", False, False, True, None, "none", "none", True),
    "business_trip": (
        "Командировка",
        "full",
        "calendar_days",
        False,
        False,
        True,
        None,
        "none",
        "all",
        True,
    ),
}
_COLUMNS = (
    "name",
    "is_paid",
    "unit",
    "reduces_main_balance",
    "requires_document",
    "requires_approval",
    "annual_limit",
    "carry_over_policy",
    "counted_non_working_days",
    "is_active",
)


async def test_every_seeded_row_matches_the_expected_table(db_session) -> None:
    rows = await _rows(db_session)

    actual = {code: tuple(row[column] for column in _COLUMNS) for code, row in rows.items()}
    assert actual == _EXPECTED


async def test_running_the_seed_again_creates_no_duplicates(db_session) -> None:
    await _run_seed(db_session)
    await _run_seed(db_session)

    result = await db_session.execute(
        text("SELECT count(*), count(DISTINCT code) FROM leave_types")
    )
    assert result.one() == (8, 8)


async def test_running_the_seed_again_does_not_overwrite_an_admin_edit(db_session) -> None:
    await db_session.execute(
        text(
            "UPDATE leave_types SET name = 'Правка администратора', is_active = TRUE, "
            "annual_limit = 5 WHERE code = 'additional_paid'"
        )
    )

    await _run_seed(db_session)

    row = (await _rows(db_session))["additional_paid"]
    assert (row["name"], row["is_active"], float(row["annual_limit"])) == (
        "Правка администратора",
        True,
        5.0,
    )


async def test_balance_is_tracked_for_exactly_the_three_accruing_types(db_session) -> None:
    """Состояние ПОСЛЕ миграции `0010` (раздел 13 п. 32), прочитанное из
    базы, а не список из кода миграции: ДА - основной, дополнительный и
    отгул, остальные пять - НЕТ. Основания значений - в докстринге `0010`."""
    rows = await _rows(db_session)

    assert {code for code, row in rows.items() if row["balance_tracked"]} == {
        "annual_paid",
        "additional_paid",
        "time_off",
    }
    assert len(rows) - sum(row["balance_tracked"] for row in rows.values()) == 5


async def test_a_type_inserted_without_the_flag_does_not_keep_a_balance(db_session) -> None:
    """Умолчание ложь на уровне БД (`0010`): повторный `seed_preset` из
    `0006` не знает колонки, а вид, добавленный мимо API, не ведёт остаток."""
    await db_session.execute(
        text(
            "INSERT INTO leave_types (code, name, is_paid, unit, reduces_main_balance, "
            "requires_document, requires_approval, carry_over_policy, "
            "counted_non_working_days, is_active) VALUES ('probe', 'Проба', 'full', "
            "'calendar_days', FALSE, FALSE, TRUE, 'none', 'all', TRUE)"
        )
    )

    rows = await _rows(db_session)

    assert rows["probe"]["balance_tracked"] is False
