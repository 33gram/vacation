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
from sqlalchemy import func, select

from app.models.employee import Employee
from app.models.leave_balance_entry import (
    ACCRUAL,
    INITIAL_IMPORT,
    RECALCULATION,
    LeaveBalanceEntry,
)
from app.modules.balances import service
from app.modules.balances.service import (
    ALREADY_ACCRUED_ENTRY_TYPES,
    AccrualClosedError,
    BalanceEntryError,
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
        old_hire_date=old,
        new_hire_date=new,
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
