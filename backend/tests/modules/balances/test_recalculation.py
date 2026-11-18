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


async def _hired(
    db_session,
    hire: datetime.date,
    suffix: str = "a",
    card_created: datetime.date | None = None,
) -> Context:
    """Сотрудник с датой приёма `hire`. Карточка по умолчанию заведена задолго до
    всех дат приёма этого файла (01.01.2020): барьер против выдуманного остатка
    (годы, закончившиеся до появления карточки, не начисляются) не мешает
    проверять сам пересчёт; барьер проверяется отдельным тестом ниже и в
    `test_lazy_accrual.py`."""
    ctx = await make_context(db_session, suffix)
    employee = await db_session.get(Employee, ctx.employee_id)
    employee.hire_date = hire
    created = card_created or D(2020, 1, 1)
    employee.created_at = datetime.datetime(
        created.year, created.month, created.day, tzinfo=datetime.UTC
    )
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


# --- FR-BAL-003/1: исходы единственного WHEN ----------------------------------


async def test_fr_bal_003_a_nonzero_difference_closes_each_old_step_with_reason_and_author(
    db_session,
) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))
    await _accrue(db_session, ctx, AS_OF)  # 18,67 за год с 10.03.2026: восемь шагов
    old_steps = [e for e in await _events(db_session, ctx) if e.entry_type == ACCRUAL]
    await _set_hire(db_session, ctx, D(2026, 3, 20))

    result = await _recalculate(db_session, ctx, D(2026, 3, 10), D(2026, 3, 20))

    recalculations = [e for e in await _events(db_session, ctx) if e.entry_type == RECALCULATION]
    # T-M2-32: зеркало на КАЖДЫЙ прежний шаг - та же дата, та же величина с минусом.
    assert len(recalculations) == len(old_steps) == 8
    mirrors = sorted(recalculations, key=lambda e: e.entry_date)
    assert [(e.entry_date, e.quantity) for e in mirrors] == [
        (step.entry_date, -step.quantity) for step in old_steps
    ]
    for event in recalculations:
        assert event.actor_account_id == ctx.actor_id
        # Журнал объясняет себя: прежняя и новая дата названы в причине.
        assert "10.03.2026" in event.reason and "20.03.2026" in event.reason
    assert sum(e.quantity for e in recalculations) == Decimal("-18.67")
    assert result.closed_years == {D(2026, 3, 10): Decimal("-18.67")}  # итог года, не событие


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


# --- решение заказчика T-M1-36: начальный остаток закрывает начисление ДО своей даты ---


def _import_event(
    ctx: Context,
    year_start: datetime.date,
    quantity: Decimal,
    on_date: datetime.date = D(2026, 1, 1),
) -> LeaveBalanceEntry:
    # `on_date` по умолчанию (01.01.2026) для года с 10.03.2026 НАМЕРЕННО лежит вне года: тесты,
    # которые берут его по умолчанию, проверяют год и барьер, а не дату, и результат от неё не
    # зависит (шагов с датой не позже 01.01.2026 в таком году нет, покрытие пусто). Для года
    # с 10.03.2025 та же дата внутри года. Не «чинить» согласованием с годом: тесты на дату
    # задают `on_date` явно.
    return LeaveBalanceEntry(
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        working_year_start=year_start,
        working_year_end=year_start.replace(year=year_start.year + 1),
        entry_type=INITIAL_IMPORT,
        quantity=quantity,
        entry_date=on_date,
        actor_account_id=ctx.actor_id,
    )


async def test_fr_bal_001_4_the_import_year_accrues_only_the_steps_after_the_import_date(
    db_session,
) -> None:
    # FR-BAL-001/4. Остаток 5 на 01.05.2026: шаги 24.03 и 24.04 (2 месяца, 4,67) покрыты им;
    # до 24.10.2026 идут шаги 3-8 (накоплено 18,67), начисляется 18,67 - 4,67 = 14,00
    # шестью событиями.
    ctx = await _hired(db_session, D(2026, 3, 10))
    db_session.add(_import_event(ctx, D(2026, 3, 10), Decimal("5"), on_date=D(2026, 5, 1)))
    await db_session.flush()

    assert await _accrue(db_session, ctx, AS_OF) == Decimal("14.00")

    events = await _events(db_session, ctx)
    assert [e.entry_type for e in events] == [INITIAL_IMPORT] + [ACCRUAL] * 6
    assert [e.entry_date for e in events[1:]] == [
        D(2026, 5, 24), D(2026, 6, 24), D(2026, 7, 24),
        D(2026, 8, 24), D(2026, 9, 24), D(2026, 10, 24),
    ]  # fmt: skip
    assert await _balance(db_session, ctx, D(2026, 3, 10)) == Decimal("19.00")
    assert await _accrue(db_session, ctx, AS_OF) == Decimal(0)  # повтор ничего не пишет


async def test_fr_bal_001_4_the_latest_import_by_date_wins_when_there_are_two(
    db_session,
) -> None:
    # Два события `initial_import` по одному виду достижимы НЕ из файла (импорт создаёт
    # карточку один раз), а прямой записью. Тест фиксирует намерение `_latest_import`: покрытие
    # задаёт САМОЕ ПОЗДНЕЕ по дате (мутация `desc` -> `asc` красит его). Остаток на 01.09.2026
    # покрывает шаги 1-6 (14,00), начисляются 24.09 и 24.10: 18,67 - 14,00 = 4,67; если бы
    # победил ранний (01.05.2026), вышло бы 14,00.
    ctx = await _hired(db_session, D(2026, 3, 10))
    db_session.add(_import_event(ctx, D(2026, 3, 10), Decimal("5"), on_date=D(2026, 5, 1)))
    db_session.add(_import_event(ctx, D(2026, 3, 10), Decimal("1"), on_date=D(2026, 9, 1)))
    await db_session.flush()

    assert await _accrue(db_session, ctx, AS_OF) == Decimal("4.67")

    events = [e for e in await _events(db_session, ctx) if e.entry_type == ACCRUAL]
    assert [e.entry_date for e in events] == [D(2026, 9, 24), D(2026, 10, 24)]


async def test_fr_bal_001_4_a_date_before_the_first_step_covers_nothing(db_session) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))
    db_session.add(_import_event(ctx, D(2026, 3, 10), Decimal("5"), on_date=D(2026, 3, 10)))
    await db_session.flush()

    assert await _accrue(db_session, ctx, AS_OF) == Decimal("18.67")  # как без импорта


async def test_fr_bal_001_4_a_step_dated_on_the_import_date_is_covered(db_session) -> None:
    # Шаг 1 датирован 24.03.2026; остаток на ту же дату его покрывает (дата "не позже").
    ctx = await _hired(db_session, D(2026, 3, 10))
    db_session.add(_import_event(ctx, D(2026, 3, 10), Decimal("5"), on_date=D(2026, 3, 24)))
    await db_session.flush()

    assert await _accrue(db_session, ctx, AS_OF) == Decimal("16.34")  # 18,67 - 2,33


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
    # T-M2-21: по событию на шаг - по 12 в каждом из трёх лет и 8 в текущем
    assert len(events) == 3 * 12 + 8
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

    # Закрыты целиком только годы ДО года импорта; год 2025 закрыт до 01.01.2026 (шаги 1-10,
    # 23,33) и начисляет шаги 11 и 12 (4,67).
    assert set(result.skipped) == {D(2023, 3, 10), D(2024, 3, 10)}
    assert result.accrued == {D(2025, 3, 10): Decimal("4.67"), D(2026, 3, 10): Decimal("18.67")}


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


# --- T-M1-36: бывшая известная дыра закрыта решением заказчика ---------------------


async def test_fr_bal_001_4_the_part_of_the_import_year_after_the_import_is_accrued(
    db_session,
) -> None:
    # Числа задачи: приём 10.03.2015, остаток занесён 01.11.2026 в рабочий год 10.03.2026-
    # 09.03.2027, as_of 09.03.2027. По FR-BAL-001/1 к 01.11.2026 отработано 7 полных месяцев
    # (10.03-09.10) и 23 дня восьмого (10.10-01.11, не меньше 15) - засчитано 8 месяцев, 28 * 8 / 12
    # = 18,67: это покрыто остатком. Остаются месяцы 9-12: 28 * 4 / 12 = 9,33 (шаги 24.11.2026,
    # 24.12.2026, 24.01.2027, 24.02.2027 по 2,33 + 2,33 + 2,34 + 2,33). Раньше не начислялось
    # ничего, и сотрудник терял 9,33 дня.
    ctx = await _hired(db_session, D(2015, 3, 10))
    db_session.add(_import_event(ctx, D(2026, 3, 10), Decimal("5"), on_date=D(2026, 11, 1)))
    await db_session.flush()

    result = await accrue_all_working_years(
        db_session,
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        as_of=D(2027, 3, 9),  # последний день года импорта
    )

    assert result.accrued == {D(2026, 3, 10): Decimal("9.33")}
    assert D(2025, 3, 10) in result.skipped  # год ДО года импорта по-прежнему закрыт целиком
    assert D(2026, 3, 10) not in result.skipped
    accruals = [e for e in await _events(db_session, ctx) if e.entry_type == ACCRUAL]
    assert [(e.entry_date, e.quantity) for e in accruals] == [
        (D(2026, 11, 24), Decimal("2.33")),
        (D(2026, 12, 24), Decimal("2.33")),
        (D(2027, 1, 24), Decimal("2.34")),
        (D(2027, 2, 24), Decimal("2.33")),
    ]
    assert await _balance(db_session, ctx, D(2026, 3, 10)) == Decimal("14.33")  # 5 + 9,33


async def test_fr_bal_001_4_years_before_the_import_year_stay_closed_as_a_whole(
    db_session,
) -> None:
    ctx = await _hired(db_session, D(2015, 3, 10))
    db_session.add(_import_event(ctx, D(2026, 3, 10), Decimal("5"), on_date=D(2026, 11, 1)))
    await db_session.flush()

    with pytest.raises(AccrualClosedError, match="начальный остаток"):
        await _accrue(db_session, ctx, D(2026, 3, 9), working_year_start=D(2025, 3, 10))
    before = len(await _events(db_session, ctx))
    result = await accrue_all_working_years(
        db_session,
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        as_of=D(2027, 3, 9),
    )
    assert D(2025, 3, 10) in result.skipped and len(await _events(db_session, ctx)) > before


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
    assert len(new_year) == 12  # T-M2-21: двенадцать шагов завершённого года
    # Автор и причина пересчёта - у КАЖДОГО события шага, не только у первого.
    for event in new_year:
        assert event.entry_type == ACCRUAL  # тип прежний: заработанное право
        assert event.actor_account_id == ctx.actor_id
        assert "10.03.2026" in event.reason and "10.03.2025" in event.reason


async def test_fr_bal_001_an_ordinary_accrual_is_still_written_without_an_author(
    db_session,
) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))

    await _accrue(db_session, ctx, AS_OF)

    events = await _events(db_session, ctx)
    assert len(events) == 8  # T-M2-21: по событию на шаг
    assert all(event.actor_account_id is None for event in events)
    assert all("Пересчёт" not in event.reason for event in events)


async def test_fr_bal_003_every_event_born_of_a_recalculation_has_author_and_reason(
    db_session,
) -> None:
    ctx = await _hired(db_session, D(2026, 3, 10))
    await _accrue(db_session, ctx, AS_OF)
    before = {e.id for e in await _events(db_session, ctx)}
    await _set_hire(db_session, ctx, D(2026, 3, 20))

    await _recalculate(db_session, ctx, D(2026, 3, 10), D(2026, 3, 20))

    born = [e for e in await _events(db_session, ctx) if e.id not in before]
    # Закрытий - по зеркалу на прежний шаг (T-M2-32), начислений по новой дате - по
    # событию на шаг (T-M2-21).
    closings = [e for e in born if e.entry_type == RECALCULATION]
    assert len(closings) == 8
    assert len([e for e in born if e.entry_type == ACCRUAL]) == len(born) - 8 > 1
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
    assert D(2025, 3, 10) in result.catch_up.skipped  # год ДО импорта закрыт им целиком
    # `FR-BAL-001/4`: год самого остатка закрыт лишь до его даты (01.01.2026, до начала года -
    # шагов нет), поэтому начисляется весь, 18,67 к 24.10.2026.
    assert result.catch_up.accrued == {D(2026, 3, 10): Decimal("18.67")}
    assert await _balance(db_session, ctx, D(2026, 3, 10)) == Decimal("23.67")


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


async def test_fr_bal_003_moving_the_hire_date_back_before_the_card_does_not_invent_the_years(
    db_session,
) -> None:
    # Карточка заведена 10.03.2026; дата приёма сдвинута на два года назад. Годы,
    # закончившиеся до появления карточки, пересчёт НЕ начисляет (барьер, T-M2-19):
    # прошлое приходит начальным остатком, а не вычисляется.
    ctx = await _hired(db_session, D(2026, 3, 10), card_created=D(2026, 3, 10))
    await _accrue(db_session, ctx, AS_OF)
    await _set_hire(db_session, ctx, D(2024, 3, 10))

    result = await _recalculate(db_session, ctx, D(2026, 3, 10), D(2024, 3, 10))

    assert result.catch_up.accrued == {}
    assert set(result.catch_up.skipped) == {D(2024, 3, 10), D(2025, 3, 10)}
