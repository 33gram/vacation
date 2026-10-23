"""Пересчёт остатка при смене даты приёма и догон начисления по всем рабочим
годам (T-M2-07, `FR-BAL-003`, `FR-BAL-001`).

Ожидаемые числа посчитаны на бумаге: приём 10.03.2026, норма 28, расчёт на
24.10.2026 даёт 18,67 (7 полных месяцев + остаток 15 дней). Новая дата
20.03.2026: 7 полных месяцев до 19.10, остаток 20.10-24.10 = 5 дней < 15,
28 * 7 / 12 = 16,33.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models.employee import Employee
from app.models.leave_balance_entry import (
    ACCRUAL,
    INITIAL_IMPORT,
    RECALCULATION,
    USAGE,
    LeaveBalanceEntry,
)
from app.modules.balances import service
from app.modules.balances.service import (
    ALREADY_ACCRUED_ENTRY_TYPES,
    AccrualClosedError,
    BalanceEntryError,
    LeaveTypeNotSeededError,
    accrue_all_working_years,
    accrue_leave,
    adjust_balance,
    get_balance,
    recalculate_balance,
)
from tests.modules.balances.helpers import Context, make_context

D = datetime.date
AS_OF = D(2026, 10, 24)


async def _hired(db_session, hire: datetime.date, suffix: str = "a") -> Context:
    ctx = await make_context(db_session, suffix)
    employee = await db_session.get(Employee, ctx.employee_id)
    employee.hire_date = hire
    await db_session.flush()
    return ctx


async def _set_hire(db_session, ctx: Context, hire: datetime.date) -> None:
    employee = await db_session.get(Employee, ctx.employee_id)
    employee.hire_date = hire
    await db_session.flush()


async def _events(db_session, ctx: Context) -> list[LeaveBalanceEntry]:
    result = await db_session.execute(
        select(LeaveBalanceEntry)
        .where(LeaveBalanceEntry.employee_id == ctx.employee_id)
        .order_by(LeaveBalanceEntry.id)
    )
    return list(result.scalars())


async def _accrue(db_session, ctx: Context, as_of: datetime.date, **kw) -> Decimal:
    return await accrue_leave(
        db_session,
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        as_of=as_of,
        **kw,
    )


async def _recalculate(db_session, ctx: Context, old: datetime.date, new: datetime.date, **kw):
    return await recalculate_balance(
        db_session,
        employee_id=ctx.employee_id,
        actor_account_id=kw.pop("actor_account_id", ctx.actor_id),
        as_of=kw.pop("as_of", AS_OF),
        changes={"hire_date": (old, new)},
    )


async def _balance(db_session, ctx: Context, year_start: datetime.date) -> Decimal:
    return await get_balance(db_session, ctx.employee_id, ctx.leave_type_id, year_start)


# --- FR-BAL-003: исходы единственного WHEN ----------------------------------


async def test_fr_bal_003_a_nonzero_difference_writes_one_event_with_reason_and_author(
    db_session,
) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))
    await _accrue(db_session, ctx, AS_OF)  # 18,67 за год с 10.03.2026
    await _set_hire(db_session, ctx, D(2026, 3, 20))

    result = await _recalculate(db_session, ctx, D(2026, 3, 10), D(2026, 3, 20))

    recalculations = [e for e in await _events(db_session, ctx) if e.entry_type == RECALCULATION]
    assert len(recalculations) == 1
    (event,) = recalculations
    assert event.quantity == Decimal("-18.67")  # РАЗНОСТЬ, закрывающая год, а не итог
    assert event.actor_account_id == ctx.actor_id
    # Журнал объясняет себя: прежняя и новая дата названы в причине.
    assert "10.03.2026" in event.reason and "20.03.2026" in event.reason
    assert result.closed_years == {D(2026, 3, 10): Decimal("-18.67")}


async def test_fr_bal_003_the_old_year_is_closed_and_the_new_year_is_accrued(db_session) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))
    await _accrue(db_session, ctx, AS_OF)
    await _set_hire(db_session, ctx, D(2026, 3, 20))

    result = await _recalculate(db_session, ctx, D(2026, 3, 10), D(2026, 3, 20))

    assert await _balance(db_session, ctx, D(2026, 3, 10)) == Decimal("0.00")
    assert await _balance(db_session, ctx, D(2026, 3, 20)) == Decimal("16.33")
    assert result.catch_up.accrued == {D(2026, 3, 20): Decimal("16.33")}


async def test_fr_bal_003_a_later_accrual_does_not_double_the_recalculated_amount(
    db_session,
) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))
    await _accrue(db_session, ctx, AS_OF)
    await _set_hire(db_session, ctx, D(2026, 3, 20))
    await _recalculate(db_session, ctx, D(2026, 3, 10), D(2026, 3, 20))
    before = len(await _events(db_session, ctx))

    written = await _accrue(db_session, ctx, AS_OF)

    assert written == 0
    assert len(await _events(db_session, ctx)) == before


async def test_fr_bal_003_a_zero_difference_writes_no_event(db_session) -> None:
    # Дата приёма сдвинута ровно на год назад: год с 10.03.2026 сохраняется, закрывать
    # нечего; пересчёт добавляет только начисление за появившийся год.
    ctx = await _hired(db_session, D(2026, 3, 10))
    await _accrue(db_session, ctx, AS_OF)
    await _set_hire(db_session, ctx, D(2025, 3, 10))

    result = await _recalculate(db_session, ctx, D(2026, 3, 10), D(2025, 3, 10))

    assert result.closed_years == {}
    assert [e for e in await _events(db_session, ctx) if e.entry_type == RECALCULATION] == []
    assert result.catch_up.accrued == {D(2025, 3, 10): Decimal("28.00")}
    assert await _balance(db_session, ctx, D(2026, 3, 10)) == Decimal("18.67")  # не тронут


async def test_fr_bal_003_an_unchanged_hire_date_writes_nothing(db_session) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))
    await _accrue(db_session, ctx, AS_OF)
    before = len(await _events(db_session, ctx))

    result = await _recalculate(db_session, ctx, D(2026, 3, 10), D(2026, 3, 10))

    assert result.closed_years == {} and result.catch_up.accrued == {}
    assert len(await _events(db_session, ctx)) == before


async def test_fr_bal_003_moving_the_hire_date_forward_past_today_closes_the_years_only(
    db_session,
) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))
    await _accrue(db_session, ctx, AS_OF)
    await _set_hire(db_session, ctx, D(2027, 3, 10))

    result = await _recalculate(db_session, ctx, D(2026, 3, 10), D(2027, 3, 10))

    assert result.closed_years == {D(2026, 3, 10): Decimal("-18.67")}
    assert result.catch_up.accrued == {}  # по новой дате ещё ни дня не отработано
    assert await _balance(db_session, ctx, D(2026, 3, 10)) == Decimal("0.00")


async def test_fr_bal_003_an_employee_without_events_gets_nothing_written(db_session) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))
    await _set_hire(db_session, ctx, D(2026, 3, 20))

    result = await _recalculate(db_session, ctx, D(2026, 3, 10), D(2026, 3, 20))

    assert result.closed_years == {} and result.catch_up.accrued == {}
    assert await _events(db_session, ctx) == []


async def test_fr_bal_003_a_vanishing_year_with_a_manual_adjustment_is_refused_and_nothing_written(
    db_session,
) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))
    await _accrue(db_session, ctx, AS_OF)
    await adjust_balance(
        db_session,
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        working_year_start=D(2026, 3, 10),
        working_year_end=D(2027, 3, 10),
        quantity=Decimal("3"),
        reason="Перенос",
        actor_account_id=ctx.actor_id,
        entry_date=D(2026, 5, 1),
    )
    await _set_hire(db_session, ctx, D(2026, 3, 20))
    before = len(await _events(db_session, ctx))

    with pytest.raises(BalanceEntryError, match="manual_adjustment"):
        await _recalculate(db_session, ctx, D(2026, 3, 10), D(2026, 3, 20))

    assert len(await _events(db_session, ctx)) == before


async def test_fr_bal_003_a_vanishing_year_with_an_initial_import_is_refused(db_session) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))
    await _accrue(db_session, ctx, AS_OF)
    db_session.add(_import_event(ctx, D(2026, 3, 10), Decimal("4")))
    await db_session.flush()
    await _set_hire(db_session, ctx, D(2026, 3, 20))
    before = len(await _events(db_session, ctx))

    with pytest.raises(BalanceEntryError, match="initial_import"):
        await _recalculate(db_session, ctx, D(2026, 3, 10), D(2026, 3, 20))

    assert len(await _events(db_session, ctx)) == before


async def test_fr_bal_003_the_author_is_required_by_the_service_not_only_by_the_database(
    db_session,
) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))
    await _accrue(db_session, ctx, AS_OF)
    await _set_hire(db_session, ctx, D(2026, 3, 20))

    with pytest.raises(BalanceEntryError, match="автор"):
        await _recalculate(db_session, ctx, D(2026, 3, 10), D(2026, 3, 20), actor_account_id=None)


@pytest.mark.parametrize("reason", ["", "   ", " \t"])
async def test_fr_bal_003_a_blank_reason_is_refused_by_the_service(db_session, reason) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))

    with pytest.raises(BalanceEntryError, match="причин"):
        await service._write_recalculation(
            db_session,
            employee_id=ctx.employee_id,
            leave_type_id=ctx.leave_type_id,
            working_year_start=D(2026, 3, 10),
            working_year_end=D(2027, 3, 10),
            quantity=Decimal("-1"),
            entry_date=AS_OF,
            reason=reason,
            actor_account_id=ctx.actor_id,
        )


async def test_fr_bal_003_a_card_without_the_new_hire_date_is_refused(db_session) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))
    await _accrue(db_session, ctx, AS_OF)

    with pytest.raises(BalanceEntryError):
        await _recalculate(db_session, ctx, D(2026, 3, 10), D(2026, 3, 20))  # карточка не менялась


# --- решение (а): recalculation считается "уже начисленным" -----------------


def test_the_already_accrued_types_are_accrual_and_recalculation_only() -> None:
    assert ALREADY_ACCRUED_ENTRY_TYPES == (ACCRUAL, RECALCULATION)
    assert INITIAL_IMPORT not in ALREADY_ACCRUED_ENTRY_TYPES


async def test_fr_bal_003_accrual_after_a_recalculation_on_the_same_date_writes_nothing(
    db_session,
) -> None:
    # Начислено 16,33 на 23.10; пересчёт +2,34 довёл право до 18,67; начисление
    # на 24.10 (положено 18,67) не должно записать те же 2,34 второй раз. На
    # наборе (ACCRUAL,) оно записало бы +2,34 и остаток стал бы 21,01.
    ctx = await _hired(db_session, D(2026, 3, 10))
    await _accrue(db_session, ctx, D(2026, 10, 23))
    db_session.add(
        LeaveBalanceEntry(
            employee_id=ctx.employee_id,
            leave_type_id=ctx.leave_type_id,
            working_year_start=D(2026, 3, 10),
            working_year_end=D(2027, 3, 10),
            entry_type=RECALCULATION,
            quantity=Decimal("2.34"),
            entry_date=D(2026, 10, 23),
            reason="Пересчёт",
            actor_account_id=ctx.actor_id,
        )
    )
    await db_session.flush()

    written = await _accrue(db_session, ctx, AS_OF)

    assert written == 0
    assert await _balance(db_session, ctx, D(2026, 3, 10)) == Decimal("18.67")


# --- решение (б): год с начальным остатком закрыт для начисления ------------


def _import_event(ctx: Context, year_start: datetime.date, quantity: Decimal) -> LeaveBalanceEntry:
    return LeaveBalanceEntry(
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        working_year_start=year_start,
        working_year_end=year_start.replace(year=year_start.year + 1),
        entry_type=INITIAL_IMPORT,
        quantity=quantity,
        entry_date=D(2026, 1, 1),
        actor_account_id=ctx.actor_id,
    )


async def test_fr_bal_001_accrual_is_silent_for_a_year_that_has_an_initial_import(
    db_session,
) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))
    db_session.add(_import_event(ctx, D(2026, 3, 10), Decimal("5")))
    await db_session.flush()

    with pytest.raises(AccrualClosedError, match="начальный остаток"):
        await _accrue(db_session, ctx, AS_OF)

    assert len(await _events(db_session, ctx)) == 1  # только импорт
    assert await _balance(db_session, ctx, D(2026, 3, 10)) == Decimal("5")


async def test_fr_bal_001_accrual_works_for_a_year_without_an_initial_import(db_session) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))

    assert await _accrue(db_session, ctx, AS_OF) == Decimal("18.67")


async def test_fr_bal_001_years_before_the_imported_one_are_closed_and_later_years_accrue(
    db_session,
) -> None:
    # Импорт в год с 10.03.2025: годы до него покрыты начальным остатком, год с
    # 10.03.2026 начисляется.
    ctx = await _hired(db_session, D(2023, 3, 10))
    db_session.add(_import_event(ctx, D(2025, 3, 10), Decimal("5")))
    await db_session.flush()

    with pytest.raises(AccrualClosedError):
        await _accrue(db_session, ctx, D(2024, 10, 24), working_year_start=D(2024, 3, 10))
    assert await _accrue(db_session, ctx, AS_OF) == Decimal("18.67")


# --- догон по всем рабочим годам --------------------------------------------


async def test_br_8_catch_up_accrues_every_working_year_since_hire(db_session) -> None:
    ctx = await _hired(db_session, D(2023, 3, 10))

    result = await accrue_all_working_years(
        db_session, employee_id=ctx.employee_id, leave_type_id=ctx.leave_type_id, as_of=AS_OF
    )

    events = [e for e in await _events(db_session, ctx) if e.entry_type == ACCRUAL]
    assert len(events) == 4  # три завершённых года и текущий
    assert result.accrued == {
        D(2023, 3, 10): Decimal("28.00"),
        D(2024, 3, 10): Decimal("28.00"),
        D(2025, 3, 10): Decimal("28.00"),
        D(2026, 3, 10): Decimal("18.67"),
    }
    assert result.skipped == {}
    assert all(e.actor_account_id is None for e in events)


async def test_br_8_a_repeated_catch_up_writes_nothing(db_session) -> None:
    ctx = await _hired(db_session, D(2023, 3, 10))
    await accrue_all_working_years(
        db_session, employee_id=ctx.employee_id, leave_type_id=ctx.leave_type_id, as_of=AS_OF
    )
    before = len(await _events(db_session, ctx))

    result = await accrue_all_working_years(
        db_session, employee_id=ctx.employee_id, leave_type_id=ctx.leave_type_id, as_of=AS_OF
    )

    assert result.accrued == {}
    assert len(await _events(db_session, ctx)) == before


async def test_br_8_catch_up_names_the_years_closed_by_an_initial_import(db_session) -> None:
    ctx = await _hired(db_session, D(2023, 3, 10))
    db_session.add(_import_event(ctx, D(2025, 3, 10), Decimal("5")))
    await db_session.flush()

    result = await accrue_all_working_years(
        db_session, employee_id=ctx.employee_id, leave_type_id=ctx.leave_type_id, as_of=AS_OF
    )

    assert set(result.skipped) == {D(2023, 3, 10), D(2024, 3, 10), D(2025, 3, 10)}
    assert result.accrued == {D(2026, 3, 10): Decimal("18.67")}


async def test_br_8_catch_up_for_a_hire_date_in_the_future_writes_nothing(db_session) -> None:
    ctx = await _hired(db_session, D(2027, 3, 10))

    result = await accrue_all_working_years(
        db_session, employee_id=ctx.employee_id, leave_type_id=ctx.leave_type_id, as_of=AS_OF
    )

    assert result.accrued == {} and await _events(db_session, ctx) == []


async def test_br_8_catch_up_refuses_a_leave_type_that_is_not_the_main_one(db_session) -> None:
    ctx = await _hired(db_session, D(2023, 3, 10))

    with pytest.raises(BalanceEntryError):
        await accrue_all_working_years(
            db_session,
            employee_id=ctx.employee_id,
            leave_type_id=ctx.other_leave_type_id,
            as_of=AS_OF,
        )

    count = await db_session.execute(select(func.count()).select_from(LeaveBalanceEntry))
    assert count.scalar_one() == 0


async def test_fr_bal_003_a_field_without_an_effect_on_the_norm_writes_nothing(db_session) -> None:
    # Поле, переданное в `changes`, но не влияющее на норму (категория, A-3.1):
    # пересчёт принимает его и событий не пишет.
    ctx = await _hired(db_session, D(2026, 3, 10))
    await _accrue(db_session, ctx, AS_OF)
    before = len(await _events(db_session, ctx))

    result = await recalculate_balance(
        db_session,
        employee_id=ctx.employee_id,
        actor_account_id=ctx.actor_id,
        as_of=AS_OF,
        changes={"leave_category": (None, "extended")},
    )

    assert result.closed_years == {} and result.catch_up.accrued == {}
    assert len(await _events(db_session, ctx)) == before


# --- ИЗВЕСТНАЯ ДЫРА (T-M1-36), зафиксированная как есть, а не как верное поведение --


async def test_known_gap_t_m1_36_the_part_of_the_import_year_after_the_import_is_never_accrued(
    db_session,
) -> None:
    # НЕ ожидаемое правильное поведение. Год импорта закрыт для начисления ЦЕЛИКОМ,
    # поэтому часть года после даты импорта не начисляется никогда: следующий год
    # стартует с нуля. Что делать (импорт с раздельными начислено/использовано;
    # импорт только на границу года; ручная корректировка HR) - решает T-M1-36;
    # когда она решит, этот тест надо заменить.
    ctx = await _hired(db_session, D(2015, 3, 10))
    db_session.add(_import_event(ctx, D(2026, 3, 10), Decimal("5")))
    await db_session.flush()

    result = await accrue_all_working_years(
        db_session,
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        as_of=D(2027, 3, 9),  # последний день года импорта
    )

    assert D(2026, 3, 10) in result.skipped  # год пропущен
    assert result.accrued == {}
    assert await _balance(db_session, ctx, D(2026, 3, 10)) == Decimal("5")  # осталось 5, не 28


# --- автор и причина у КАЖДОЙ записи, порождённой пересчётом (FR-BAL-003) -----


async def test_fr_bal_003_a_shift_by_whole_years_leaves_a_record_with_author_and_reason(
    db_session,
) -> None:
    # Ни один год не исчезает (прежний год остаётся годовщиной), закрывающих строк
    # нет; запись с автором и причиной, названной сменой даты, - начисление по
    # новому раннему году.
    ctx = await _hired(db_session, D(2026, 3, 10))
    await _accrue(db_session, ctx, AS_OF)
    await _set_hire(db_session, ctx, D(2025, 3, 10))

    await _recalculate(db_session, ctx, D(2026, 3, 10), D(2025, 3, 10))

    new_year = [e for e in await _events(db_session, ctx) if e.working_year_start == D(2025, 3, 10)]
    assert len(new_year) == 1
    (event,) = new_year
    assert event.entry_type == ACCRUAL  # тип прежний: заработанное право
    assert event.actor_account_id == ctx.actor_id
    assert "10.03.2026" in event.reason and "10.03.2025" in event.reason


async def test_fr_bal_001_an_ordinary_accrual_is_still_written_without_an_author(
    db_session,
) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))

    await _accrue(db_session, ctx, AS_OF)

    (event,) = await _events(db_session, ctx)
    assert event.actor_account_id is None
    assert "Пересчёт" not in event.reason


async def test_fr_bal_003_every_event_born_of_a_recalculation_has_author_and_reason(
    db_session,
) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))
    await _accrue(db_session, ctx, AS_OF)
    before = {e.id for e in await _events(db_session, ctx)}
    await _set_hire(db_session, ctx, D(2026, 3, 20))

    await _recalculate(db_session, ctx, D(2026, 3, 10), D(2026, 3, 20))

    born = [e for e in await _events(db_session, ctx) if e.id not in before]
    assert len(born) == 2  # закрытие и начисление
    assert all(e.actor_account_id == ctx.actor_id and e.reason for e in born)


# --- состав событий и сдвиги --------------------------------------------------


async def test_fr_bal_003_a_vanishing_year_with_a_usage_event_is_refused(db_session) -> None:
    # Правило - «любое, кроме accrual/recalculation»; проверено не только на двух типах.
    ctx = await _hired(db_session, D(2026, 3, 10))
    await _accrue(db_session, ctx, AS_OF)
    db_session.add(
        LeaveBalanceEntry(
            employee_id=ctx.employee_id,
            leave_type_id=ctx.leave_type_id,
            working_year_start=D(2026, 3, 10),
            working_year_end=D(2027, 3, 10),
            entry_type=USAGE,
            quantity=Decimal("-2"),
            entry_date=D(2026, 6, 1),
        )
    )
    await db_session.flush()
    await _set_hire(db_session, ctx, D(2026, 3, 20))
    before = len(await _events(db_session, ctx))

    with pytest.raises(BalanceEntryError, match="usage"):
        await _recalculate(db_session, ctx, D(2026, 3, 10), D(2026, 3, 20))

    assert len(await _events(db_session, ctx)) == before


async def test_fr_bal_003_a_shift_by_whole_years_keeps_an_import_in_the_surviving_year(
    db_session,
) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))
    db_session.add(_import_event(ctx, D(2026, 3, 10), Decimal("5")))
    await db_session.flush()
    await _set_hire(db_session, ctx, D(2025, 3, 10))

    result = await _recalculate(db_session, ctx, D(2026, 3, 10), D(2025, 3, 10))

    assert result.closed_years == {}  # год с импортом сохранился - отказа нет
    assert D(2025, 3, 10) in result.catch_up.skipped  # год ДО импорта закрыт им
    assert D(2026, 3, 10) in result.catch_up.skipped
    assert await _balance(db_session, ctx, D(2026, 3, 10)) == Decimal("5")


async def test_fr_bal_003_moving_the_hire_date_back_accrues_the_years_before_the_old_date(
    db_session,
) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))
    await _accrue(db_session, ctx, AS_OF)
    await _set_hire(db_session, ctx, D(2024, 3, 10))

    result = await _recalculate(db_session, ctx, D(2026, 3, 10), D(2024, 3, 10))

    assert result.closed_years == {}
    assert result.catch_up.accrued == {
        D(2024, 3, 10): Decimal("28.00"),
        D(2025, 3, 10): Decimal("28.00"),
    }
    assert await _balance(db_session, ctx, D(2026, 3, 10)) == Decimal("18.67")  # не тронут


async def test_fr_bal_003_several_years_with_mixed_events_survive_a_shift_by_whole_years(
    db_session,
) -> None:
    ctx = await _hired(db_session, D(2024, 3, 10))
    await accrue_all_working_years(
        db_session, employee_id=ctx.employee_id, leave_type_id=ctx.leave_type_id, as_of=AS_OF
    )
    await adjust_balance(
        db_session,
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        working_year_start=D(2025, 3, 10),
        working_year_end=D(2026, 3, 10),
        quantity=Decimal("3"),
        reason="Перенос",
        actor_account_id=ctx.actor_id,
        entry_date=D(2025, 9, 1),
    )
    await _set_hire(db_session, ctx, D(2023, 3, 10))

    result = await _recalculate(db_session, ctx, D(2024, 3, 10), D(2023, 3, 10))

    assert result.closed_years == {}  # все старые годы - годовщины и при новой дате
    assert result.catch_up.accrued == {D(2023, 3, 10): Decimal("28.00")}
    assert await _balance(db_session, ctx, D(2025, 3, 10)) == Decimal("31.00")  # 28 + 3


async def test_fr_bal_003_there_and_back_again_restores_the_original_years(db_session) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))
    await _accrue(db_session, ctx, AS_OF)
    await _set_hire(db_session, ctx, D(2026, 3, 20))
    await _recalculate(db_session, ctx, D(2026, 3, 10), D(2026, 3, 20))
    await _set_hire(db_session, ctx, D(2026, 3, 10))

    await _recalculate(db_session, ctx, D(2026, 3, 20), D(2026, 3, 10))

    assert await _balance(db_session, ctx, D(2026, 3, 10)) == Decimal("18.67")
    assert await _balance(db_session, ctx, D(2026, 3, 20)) == Decimal("0.00")


# --- конфигурация, а не запрос -------------------------------------------------


async def test_fr_bal_003_a_missing_main_leave_type_is_a_configuration_error_not_a_bad_request(
    db_session,
) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))
    await db_session.execute(text("DELETE FROM leave_types WHERE code = 'annual_paid'"))
    await _set_hire(db_session, ctx, D(2026, 3, 20))

    with pytest.raises(LeaveTypeNotSeededError) as excinfo:
        await _recalculate(db_session, ctx, D(2026, 3, 10), D(2026, 3, 20))

    assert not isinstance(excinfo.value, BalanceEntryError)  # эндпоинт не переведёт в 400
