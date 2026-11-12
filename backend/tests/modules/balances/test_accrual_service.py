"""Запись начисления в журнал, остаток на дату и сумма по годам
(T-M2-05, `FR-BAL-001`, `BR-8`).

Арифметика — `test_accrual_arithmetic.py`; здесь — что делает сервис с
журналом. Ожидаемые числа посчитаны на бумаге: приём 10.03.2026, норма 28.
`FR-BAL-002`/`FR-BAL-004` покрыты в `test_service.py` и не повторяются.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models.employee import Employee
from app.models.leave_balance_entry import ACCRUAL, INITIAL_IMPORT, LeaveBalanceEntry
from app.modules.balances.service import (
    MAIN_LEAVE_TYPE_CODE,
    BalanceEntryError,
    accrue_leave,
    adjust_balance,
    get_accrual_years,
    get_balance,
    get_balances_by_working_year,
    get_total_balance,
    list_entries,
)
from tests.modules.balances.helpers import Context, make_context

D = datetime.date
HIRE = D(2026, 3, 10)
YEAR_START = D(2026, 3, 10)


async def _hired(db_session, suffix: str = "a") -> Context:
    ctx = await make_context(db_session, suffix)
    employee = await db_session.get(Employee, ctx.employee_id)
    employee.hire_date = HIRE
    await db_session.flush()
    return ctx


async def _accrue(db_session, ctx: Context, as_of: datetime.date, **kw) -> Decimal:
    return await accrue_leave(
        db_session,
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        as_of=as_of,
        **kw,
    )


async def _events(db_session, ctx: Context) -> list[LeaveBalanceEntry]:
    result = await db_session.execute(
        select(LeaveBalanceEntry)
        .where(LeaveBalanceEntry.employee_id == ctx.employee_id)
        .order_by(LeaveBalanceEntry.id)
    )
    return list(result.scalars())


async def test_fr_bal_001_accrual_writes_one_positive_system_event_per_step_of_the_year(
    db_session,
) -> None:
    # T-M2-21: до 24.10 набралось восемь шагов (24.03 ... 24.10), по событию на шаг,
    # каждое датировано днём шага; сумма - прежние 18,67.
    ctx = await _hired(db_session)

    written = await _accrue(db_session, ctx, D(2026, 10, 24))

    events = await _events(db_session, ctx)
    assert written == Decimal("18.67")
    assert [event.entry_date for event in events] == [D(2026, month, 24) for month in range(3, 11)]
    assert [event.quantity for event in events] == [
        Decimal("2.33"), Decimal("2.34"), Decimal("2.33"), Decimal("2.33"),
        Decimal("2.34"), Decimal("2.33"), Decimal("2.33"), Decimal("2.34"),
    ]  # fmt: skip
    assert {(e.entry_type, e.actor_account_id) for e in events} == {(ACCRUAL, None)}
    assert {(e.working_year_start, e.working_year_end) for e in events} == {
        (YEAR_START, D(2027, 3, 10))
    }
    assert "8-й месяц" in events[-1].reason and "24.10.2026" in events[-1].reason
    assert await get_balance(db_session, ctx.employee_id, ctx.leave_type_id, YEAR_START) == (
        Decimal("18.67")
    )


async def test_fr_bal_001_a_repeated_call_for_the_same_date_writes_nothing(db_session) -> None:
    ctx = await _hired(db_session)
    await _accrue(db_session, ctx, D(2026, 10, 24))

    written = await _accrue(db_session, ctx, D(2026, 10, 24))

    assert written == 0
    assert len(await _events(db_session, ctx)) == 8


async def test_fr_bal_001_a_later_date_writes_only_the_difference(db_session) -> None:
    # 10.12: девятый шаг (24.11) - 28 * 9 / 12 = 21,00; уже начислено 18,67 (восемь шагов),
    # десятый шаг (24.12) ещё впереди.
    ctx = await _hired(db_session)
    await _accrue(db_session, ctx, D(2026, 10, 24))

    written = await _accrue(db_session, ctx, D(2026, 12, 10))

    events = await _events(db_session, ctx)
    assert written == Decimal("2.33")
    assert len(events) == 9
    assert (events[-1].quantity, events[-1].entry_date) == (Decimal("2.33"), D(2026, 11, 24))
    assert await get_balance(db_session, ctx.employee_id, ctx.leave_type_id, YEAR_START) == (
        Decimal("21.00")
    )


async def test_fr_bal_001_a_zero_or_negative_difference_writes_no_event_and_does_not_fail(
    db_session,
) -> None:
    # Уже начислено 21,00 (на 10.12); расчёт на более раннюю дату даёт 18,67 -
    # разность отрицательна. accrual строго положителен по CHECK, а
    # уменьшение накопленного - recalculation (T-M2-07), не начисление.
    ctx = await _hired(db_session)
    await _accrue(db_session, ctx, D(2026, 12, 10))

    written = await _accrue(db_session, ctx, D(2026, 10, 24))

    assert written == 0
    assert len(await _events(db_session, ctx)) == 9
    assert await get_balance(db_session, ctx.employee_id, ctx.leave_type_id, YEAR_START) == (
        Decimal("21.00")
    )


async def test_fr_bal_001_nothing_to_accrue_on_the_hire_date(db_session) -> None:
    ctx = await _hired(db_session)

    assert await _accrue(db_session, ctx, HIRE) == 0
    assert await _events(db_session, ctx) == []


async def test_fr_bal_001_the_norm_comes_from_business_rules_config_not_from_code(
    db_session,
) -> None:
    await db_session.execute(
        text("UPDATE business_rules_config SET value = '30' WHERE key = 'annual_leave_days'")
    )
    ctx = await _hired(db_session)

    written = await _accrue(db_session, ctx, D(2026, 10, 24))

    assert written == Decimal("20.00")  # 30 * 8 / 12


async def test_fr_bal_001_the_rounding_threshold_comes_from_business_rules_config(
    db_session,
) -> None:
    await db_session.execute(
        text(
            "UPDATE business_rules_config SET value = '14' "
            "WHERE key = 'month_rounding_threshold_days'"
        )
    )
    ctx = await _hired(db_session)

    written = await _accrue(db_session, ctx, D(2026, 10, 23))  # остаток 14 дней

    assert written == Decimal("18.67")  # 8 месяцев


async def test_fr_bal_001_excluded_days_shift_the_recorded_working_year_end(db_session) -> None:
    ctx = await _hired(db_session)

    await _accrue(db_session, ctx, D(2027, 3, 25), excluded_days=16, working_year_start=HIRE)

    events = await _events(db_session, ctx)
    assert {event.working_year_end for event in events} == {D(2027, 3, 26)}
    assert sum(event.quantity for event in events) == Decimal("28.00")
    assert events[-1].entry_date == D(2027, 3, 12)  # шаг 24.02.2027, сдвинутый на 16 дней


async def test_fr_bal_001_an_unknown_employee_is_refused_and_nothing_is_written(
    db_session,
) -> None:
    ctx = await _hired(db_session)

    with pytest.raises(BalanceEntryError):
        await accrue_leave(
            db_session,
            employee_id=ctx.employee_id + 10_000,
            leave_type_id=ctx.leave_type_id,
            as_of=D(2026, 10, 24),
        )

    count = await db_session.execute(select(func.count()).select_from(LeaveBalanceEntry))
    assert count.scalar_one() == 0


async def test_fr_bal_001_accrual_is_refused_for_a_leave_type_that_is_not_the_main_one(
    db_session,
) -> None:
    ctx = await _hired(db_session)

    with pytest.raises(BalanceEntryError):
        await accrue_leave(
            db_session,
            employee_id=ctx.employee_id,
            leave_type_id=ctx.other_leave_type_id,  # unpaid
            as_of=D(2026, 10, 24),
        )

    assert await _events(db_session, ctx) == []


async def test_fr_bal_001_a_missing_leave_type_is_reported_as_missing_not_as_non_main(
    db_session,
) -> None:
    ctx = await _hired(db_session)

    with pytest.raises(BalanceEntryError, match="Вид отпуска не найден"):
        await accrue_leave(
            db_session,
            employee_id=ctx.employee_id,
            leave_type_id=10_000_000,
            as_of=D(2026, 10, 24),
        )

    assert await _events(db_session, ctx) == []


async def test_fr_bal_001_a_refused_leave_type_takes_no_lock_on_the_employee(db_session) -> None:
    # Проверка вида идёт до `FOR UPDATE`: отказ по виду не должен читать строку
    # сотрудника с блокировкой. Фиксируем порядок по запросам.
    from sqlalchemy import event

    ctx = await _hired(db_session)
    statements: list[str] = []
    connection = await db_session.connection()

    def capture(conn, cursor, statement, *args):
        statements.append(statement)

    sync_engine = connection.sync_engine
    event.listen(sync_engine, "before_cursor_execute", capture)
    try:
        with pytest.raises(BalanceEntryError):
            await accrue_leave(
                db_session,
                employee_id=ctx.employee_id,
                leave_type_id=ctx.other_leave_type_id,
                as_of=D(2026, 10, 24),
            )
    finally:
        event.remove(sync_engine, "before_cursor_execute", capture)

    assert not any("FOR UPDATE" in statement.upper() for statement in statements)


async def test_the_statement_capture_does_see_the_lock_on_the_happy_path(db_session) -> None:
    # Положительный контроль к тесту выше: тот же перехват на успешном вызове
    # ВИДИТ `FOR UPDATE`, значит отсутствие блокировки при отказе - не пустой
    # перехват.
    from sqlalchemy import event

    ctx = await _hired(db_session)
    statements: list[str] = []
    connection = await db_session.connection()

    def capture(conn, cursor, statement, *args):
        statements.append(statement)

    sync_engine = connection.sync_engine
    event.listen(sync_engine, "before_cursor_execute", capture)
    try:
        await _accrue(db_session, ctx, D(2026, 10, 24))
    finally:
        event.remove(sync_engine, "before_cursor_execute", capture)

    assert any("FOR UPDATE" in statement.upper() for statement in statements)


def test_the_main_leave_type_code_is_one_of_the_codes_seeded_by_the_migrations() -> None:
    # `MAIN_LEAVE_TYPE_CODE` - второй набранный руками экземпляр кода из
    # миграции видов отпуска; импортировать миграцию в приложение нельзя, поэтому
    # переименование в справочнике ловит этот тест. Миграции ОБНАРУЖИВАЮТСЯ:
    # берутся все, у кого есть таблица `_LEAVE_TYPES` и `_PRESET`.
    import importlib.util
    from pathlib import Path

    versions = Path(__file__).resolve().parents[3] / "alembic" / "versions"
    codes: set[str] = set()
    for path in sorted(versions.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"codes_{path.stem}", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        table = getattr(module, "_LEAVE_TYPES", None)
        preset = getattr(module, "_PRESET", None)
        if table is not None and table.name == "leave_types" and preset is not None:
            codes |= {row["code"] for row in preset}

    assert codes, "ни одна миграция с видами отпуска не найдена - сторож ослеп"
    assert MAIN_LEAVE_TYPE_CODE in codes


async def test_fr_bal_001_a_manual_adjustment_is_not_counted_as_already_accrued(db_session) -> None:
    # Набор ALREADY_ACCRUED_ENTRY_TYPES - только `accrual`: корректировка не
    # уменьшает начисление (поведение зафиксировано, см. оговорку у константы).
    ctx = await _hired(db_session)
    await _adjust(db_session, ctx, YEAR_START, "5", D(2026, 5, 1))

    written = await _accrue(db_session, ctx, D(2026, 10, 24))

    assert written == Decimal("18.67")


async def test_fr_bal_001_a_calculation_date_before_the_hire_date_is_refused(db_session) -> None:
    ctx = await _hired(db_session)

    with pytest.raises(BalanceEntryError):
        await _accrue(db_session, ctx, D(2026, 3, 9))

    assert await _events(db_session, ctx) == []


async def test_br_8_balance_on_a_date_before_and_after_the_event(db_session) -> None:
    ctx = await _hired(db_session)
    await _accrue(db_session, ctx, D(2026, 10, 24))

    def balance(as_of: datetime.date):
        return get_balance(
            db_session, ctx.employee_id, ctx.leave_type_id, YEAR_START, as_of=as_of
        )

    assert await balance(D(2026, 3, 23)) == 0
    assert await balance(D(2026, 10, 23)) == Decimal("16.33")  # семь шагов
    assert await balance(D(2026, 10, 24)) == Decimal("18.67")  # день шага включается
    assert await balance(D(2026, 12, 31)) == Decimal("18.67")


async def test_br_8_balance_on_a_future_date_does_not_grow_without_an_accrual(db_session) -> None:
    # Граница из докстринга get_balance: функция отражает ЗАПИСАННОЕ.
    ctx = await _hired(db_session)
    await _accrue(db_session, ctx, D(2026, 10, 24))

    future = await get_balance(
        db_session, ctx.employee_id, ctx.leave_type_id, YEAR_START, as_of=D(2027, 3, 1)
    )

    assert future == Decimal("18.67")  # не 28: начисления на эту дату в журнале нет


async def test_br_8_without_a_date_the_balance_is_all_events_as_before(db_session) -> None:
    ctx = await _hired(db_session)
    await _accrue(db_session, ctx, D(2026, 12, 10))

    assert await get_balance(db_session, ctx.employee_id, ctx.leave_type_id, YEAR_START) == (
        Decimal("21.00")
    )


async def _adjust(db_session, ctx: Context, year_start: datetime.date, quantity: str, on: D):
    return await adjust_balance(
        db_session,
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        working_year_start=year_start,
        working_year_end=year_start.replace(year=year_start.year + 1),
        quantity=Decimal(quantity),
        reason="Перенос остатков",
        actor_account_id=ctx.actor_id,
        entry_date=on,
    )


async def test_br_8_total_balance_sums_every_working_year(db_session) -> None:
    ctx = await _hired(db_session)
    await _adjust(db_session, ctx, D(2024, 3, 10), "5", D(2025, 1, 10))
    await _adjust(db_session, ctx, D(2025, 3, 10), "12.5", D(2025, 6, 1))
    await _accrue(db_session, ctx, D(2026, 10, 24))

    by_year = await get_balances_by_working_year(db_session, ctx.employee_id, ctx.leave_type_id)
    total = await get_total_balance(db_session, ctx.employee_id, ctx.leave_type_id)

    assert by_year == {
        D(2024, 3, 10): Decimal("5"),
        D(2025, 3, 10): Decimal("12.5"),
        D(2026, 3, 10): Decimal("18.67"),
    }
    assert total == Decimal("36.17")


async def test_br_8_total_balance_on_a_date_counts_only_events_up_to_it(db_session) -> None:
    ctx = await _hired(db_session)
    await _adjust(db_session, ctx, D(2024, 3, 10), "5", D(2025, 1, 10))
    await _adjust(db_session, ctx, D(2025, 3, 10), "12.5", D(2025, 6, 1))

    total = await get_total_balance(
        db_session, ctx.employee_id, ctx.leave_type_id, as_of=D(2025, 3, 1)
    )

    assert total == Decimal("5")


async def test_br_8_total_balance_of_an_employee_without_events_is_zero(db_session) -> None:
    ctx = await _hired(db_session)

    assert await get_total_balance(db_session, ctx.employee_id, ctx.leave_type_id) == 0


# --- свёртка начислений по рабочим годам (T-M2-30) --------------------------------


async def _years(db_session, ctx: Context, **kw):
    return await get_accrual_years(db_session, ctx.employee_id, ctx.leave_type_id, **kw)


async def test_t_m2_30_the_accrual_years_sum_the_steps_with_the_year_bounds_and_the_last_date(
    db_session,
) -> None:
    ctx = await _hired(db_session)
    await _accrue(db_session, ctx, D(2026, 10, 24))  # восемь шагов, 24.03 ... 24.10

    (year,) = await _years(db_session, ctx)

    assert (year.working_year_start, year.working_year_end) == (YEAR_START, D(2027, 3, 10))
    assert year.accrued == Decimal("18.67")
    assert year.last_accrual_date == D(2026, 10, 24)


async def test_t_m2_30_the_sum_follows_a_later_accrual_and_a_cut_date(db_session) -> None:
    ctx = await _hired(db_session)
    await _accrue(db_session, ctx, D(2026, 12, 10))  # девять шагов, последний 24.11

    (full,) = await _years(db_session, ctx)
    (cut,) = await _years(db_session, ctx, as_of=D(2026, 10, 24))

    assert (full.accrued, full.last_accrual_date) == (Decimal("21.00"), D(2026, 11, 24))
    assert (cut.accrued, cut.last_accrual_date) == (Decimal("18.67"), D(2026, 10, 24))
    assert await _years(db_session, ctx, as_of=D(2026, 3, 23)) == []  # до первого шага


async def test_t_m2_30_the_year_sum_is_the_balance_of_the_year_when_only_accruals_exist(
    db_session,
) -> None:
    # Одни и те же правила суммирования, что у `get_balance`: при журнале из одних начислений
    # итог года совпадает с остатком года на каждую дату.
    ctx = await _hired(db_session)
    await _accrue(db_session, ctx, D(2026, 12, 10))

    for day in (D(2026, 3, 24), D(2026, 6, 1), D(2026, 11, 24), D(2026, 12, 10)):
        (year,) = await _years(db_session, ctx, as_of=day)
        balance = await get_balance(
            db_session, ctx.employee_id, ctx.leave_type_id, YEAR_START, as_of=day
        )
        assert year.accrued == balance, day


async def test_t_m2_30_other_event_types_are_not_summed_and_the_history_can_leave_accruals_out(
    db_session,
) -> None:
    ctx = await _hired(db_session)
    await _accrue(db_session, ctx, D(2026, 10, 24))
    await adjust_balance(
        db_session,
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        working_year_start=YEAR_START,
        working_year_end=D(2027, 3, 10),
        quantity=Decimal("3"),
        reason="Перенос",
        actor_account_id=ctx.actor_id,
        entry_date=D(2026, 10, 24),
    )

    (year,) = await _years(db_session, ctx)
    kept = await list_entries(
        db_session, ctx.employee_id, ctx.leave_type_id, include_accruals=False
    )
    everything = await list_entries(db_session, ctx.employee_id, ctx.leave_type_id)

    assert year.accrued == Decimal("18.67")  # корректировка +3 в итог начислений не входит
    assert [e.entry_type for e in kept] == ["manual_adjustment"]
    assert len(everything) == 9 and everything[0].entry_type == ACCRUAL  # по умолчанию - все


async def test_t_m2_30_a_year_without_accrual_events_and_an_employee_without_events_are_absent(
    db_session,
) -> None:
    ctx = await _hired(db_session)
    assert await _years(db_session, ctx) == []  # сотрудник без событий

    db_session.add(
        LeaveBalanceEntry(
            employee_id=ctx.employee_id,
            leave_type_id=ctx.leave_type_id,
            working_year_start=YEAR_START,
            working_year_end=D(2027, 3, 10),
            entry_type=INITIAL_IMPORT,
            quantity=Decimal("12.5"),
            entry_date=D(2026, 4, 1),
            actor_account_id=ctx.actor_id,
        )
    )
    await db_session.flush()

    assert await _years(db_session, ctx) == []  # начальный остаток - не начисление
