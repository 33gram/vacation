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


async def test_attributes_stated_by_the_requirements(db_session) -> None:
    rows = await _rows(db_session)

    # FR-SICK-001: больничный не уменьшает остаток основного отпуска, нужны номер и даты ЭЛН.
    assert rows["sick"]["reduces_main_balance"] is False
    assert rows["sick"]["requires_document"] is True
    # Раздел 6, п. 8: командировка не расходует остаток отпуска.
    assert rows["business_trip"]["reduces_main_balance"] is False
    # BR-9: отпуск без сохранения зарплаты неоплачиваемый.
    assert rows["unpaid"]["is_paid"] == "none"
    # BR-1: основной отпуск — календарные дни, уменьшает основной остаток.
    assert rows["annual_paid"]["unit"] == "calendar_days"
    assert rows["annual_paid"]["reduces_main_balance"] is True
    # Нормы закона не дублируются в колонке вида (правило 5, T-M2-03).
    assert all(row["annual_limit"] is None for row in rows.values())


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


async def test_counted_non_working_days_follow_art_120_and_the_unit(db_session) -> None:
    rows = await _rows(db_session)

    # Ст. 120 ТК РФ: у ежегодных отпусков праздники не входят, выходные входят.
    assert rows["annual_paid"]["counted_non_working_days"] == "weekends_only"
    assert rows["additional_paid"]["counted_non_working_days"] == "weekends_only"
    # Вид в рабочих днях: ни выходные, ни праздники не входят.
    assert rows["time_off"]["counted_non_working_days"] == "none"
    assert {
        code for code, row in rows.items() if row["counted_non_working_days"] == "all"
    } == {"unpaid", "study", "maternity_childcare", "sick", "business_trip"}
