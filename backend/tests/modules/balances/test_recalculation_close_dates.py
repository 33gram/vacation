"""Критерий приёмки T-M2-32 (`BR-8`, `FR-BAL-003`): после смены даты приёма сумма остатков
по ВСЕМ рабочим годам на любую дату равна расчёту по НОВОЙ дате приёма, а остаток исчезнувшего
года на любую дату - нулю, потому что закрытие датировано днями прежних шагов, а не днём
пересчёта. Проверка - перебором КАЖДОГО дня от приёма до дня пересчёта.

Исходные данные - те же, что в измерении задачи: приём 10.03.2026, начислено на 24.10.2026,
пересчёт в тот же день.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.employee import Employee
from app.models.leave_balance_entry import ACCRUAL, RECALCULATION, LeaveBalanceEntry
from app.modules.balances.accrual import compute_accrual, working_year_starts
from app.modules.balances.service import (
    accrue_all_working_years,
    accrue_leave,
    get_balance,
    get_total_balance,
    recalculate_balance,
)
from tests.modules.balances.helpers import Context, make_context

D = datetime.date
ONE_DAY = datetime.timedelta(days=1)
OLD_HIRE = D(2026, 3, 10)
AS_OF = D(2026, 10, 24)

# (новая дата приёма, ожидается ли исчезновение прежнего года)
SHIFTS = [
    pytest.param(D(2026, 3, 11), True, id="+1 day"),
    pytest.param(D(2026, 3, 20), True, id="+10 days"),
    pytest.param(D(2026, 4, 10), True, id="+1 month"),
    pytest.param(D(2026, 6, 10), True, id="+3 months"),
    pytest.param(D(2025, 9, 10), True, id="-6 months"),
    pytest.param(D(2025, 3, 10), False, id="-1 year (whole years)"),
]


async def _hired(db_session, hire: datetime.date) -> Context:
    ctx = await make_context(db_session, "close")
    employee = await db_session.get(Employee, ctx.employee_id)
    employee.hire_date = hire
    employee.created_at = datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
    await db_session.flush()
    return ctx


async def _events(db_session, ctx: Context) -> list[LeaveBalanceEntry]:
    result = await db_session.execute(
        select(LeaveBalanceEntry)
        .where(LeaveBalanceEntry.employee_id == ctx.employee_id)
        .order_by(LeaveBalanceEntry.id)
    )
    return list(result.scalars())


async def _accrue(db_session, ctx: Context, as_of: datetime.date) -> None:
    await accrue_leave(
        db_session, employee_id=ctx.employee_id, leave_type_id=ctx.leave_type_id, as_of=as_of
    )


async def _shift(db_session, ctx: Context, old: datetime.date, new: datetime.date, **kw):
    employee = await db_session.get(Employee, ctx.employee_id)
    employee.hire_date = new
    await db_session.flush()
    return await recalculate_balance(
        db_session,
        employee_id=ctx.employee_id,
        actor_account_id=ctx.actor_id,
        as_of=kw.pop("as_of", AS_OF),
        changes={"hire_date": (old, new)},
    )


async def _accrue_all(db_session, ctx: Context, as_of: datetime.date) -> None:
    await accrue_all_working_years(
        db_session, employee_id=ctx.employee_id, leave_type_id=ctx.leave_type_id, as_of=as_of
    )


def _expected_total(hire: datetime.date, day: datetime.date) -> Decimal:
    """Сумма `compute_accrual` по всем рабочим годам новой даты приёма на `day`; для
    завершённого года расчёт берётся на его последний день."""
    starts = working_year_starts(hire, day)
    total = Decimal(0)
    for index, start in enumerate(starts):
        last_day = day if index == len(starts) - 1 else starts[index + 1] - ONE_DAY
        total += compute_accrual(
            hire_date=hire,
            as_of=last_day,
            annual_norm=28,
            rounding_threshold_days=15,
            excluded_days=0,
            working_year_start=start,
        ).accrued
    return total


async def _assert_total_matches(
    db_session,
    ctx: Context,
    hire: datetime.date,
    first: datetime.date,
    *,
    stop: datetime.date | None = None,
) -> None:
    """Сумма по годам на КАЖДЫЙ день от `first` до дня пересчёта равна расчёту по `hire`
    (`stop` - дата увольнения: расчёт на более поздние дни стоит на ней)."""
    for day in _days(first, AS_OF):
        total = await get_total_balance(db_session, ctx.employee_id, ctx.leave_type_id, as_of=day)
        expected = _expected_total(hire, min(day, stop) if stop else day)
        assert total == expected, f"сумма по годам на {day}"


def _days(first: datetime.date, last: datetime.date):
    day = first
    while day <= last:
        yield day
        day += ONE_DAY


@pytest.mark.parametrize(("new_hire", "vanishes"), SHIFTS)
async def test_br_8_the_total_on_every_day_equals_the_accrual_by_the_new_hire_date(
    db_session, new_hire, vanishes
) -> None:
    ctx = await _hired(db_session, OLD_HIRE)
    await _accrue(db_session, ctx, AS_OF)

    await _shift(db_session, ctx, OLD_HIRE, new_hire)

    # Прежний год исчез ровно тогда, когда это ожидается (при сдвиге на целые годы - нет).
    closings = [e for e in await _events(db_session, ctx) if e.entry_type == RECALCULATION]
    assert bool(closings) == vanishes
    await _assert_total_matches(db_session, ctx, new_hire, min(OLD_HIRE, new_hire))


@pytest.mark.parametrize(("new_hire", "vanishes"), SHIFTS)
async def test_br_8_the_vanished_year_is_zero_on_every_day(db_session, new_hire, vanishes) -> None:
    ctx = await _hired(db_session, OLD_HIRE)
    await _accrue(db_session, ctx, AS_OF)

    await _shift(db_session, ctx, OLD_HIRE, new_hire)

    closings = [e for e in await _events(db_session, ctx) if e.entry_type == RECALCULATION]
    if not vanishes:
        # Сдвиг на целое число лет: год сохраняется, закрывать нечего.
        assert closings == []
        return
    for day in _days(OLD_HIRE, AS_OF):
        balance = await get_balance(
            db_session, ctx.employee_id, ctx.leave_type_id, OLD_HIRE, as_of=day
        )
        assert balance == 0, f"остаток прежнего года на {day}"


async def test_fr_bal_003_every_mirror_event_has_the_date_amount_author_and_reason_of_its_step(
    db_session,
) -> None:
    ctx = await _hired(db_session, OLD_HIRE)
    await _accrue(db_session, ctx, AS_OF)
    steps = [e for e in await _events(db_session, ctx) if e.entry_type == ACCRUAL]

    await _shift(db_session, ctx, OLD_HIRE, D(2026, 3, 20))

    mirrors = [e for e in await _events(db_session, ctx) if e.entry_type == RECALCULATION]
    assert len(steps) == len(mirrors) == 8
    for step, mirror in zip(steps, sorted(mirrors, key=lambda e: e.id), strict=True):
        assert (mirror.entry_date, mirror.quantity) == (step.entry_date, -step.quantity)
        assert mirror.actor_account_id == ctx.actor_id and mirror.reason.strip()


async def test_fr_bal_003_a_repeated_recalculation_does_not_close_the_year_twice(
    db_session,
) -> None:
    # Идемпотентность держится на нуле: сумма начислений и закрытий года после закрытия
    # (ALREADY_ACCRUED_ENTRY_TYPES) равна нулю, значит закрывать нечего.
    ctx = await _hired(db_session, OLD_HIRE)
    await _accrue(db_session, ctx, AS_OF)
    await _shift(db_session, ctx, OLD_HIRE, D(2026, 3, 20))
    written = len(await _events(db_session, ctx))

    result = await _shift(db_session, ctx, OLD_HIRE, D(2026, 3, 20))

    assert len(await _events(db_session, ctx)) == written
    assert result.closed_years == {}
    await _assert_total_matches(db_session, ctx, D(2026, 3, 20), OLD_HIRE)


async def test_br_8_returning_the_hire_date_accrues_the_year_again_and_the_total_is_right(
    db_session,
) -> None:
    # Год снова существует, его закрытые начисления в сумме уже начисленного дают ноль
    # (`recalculation` входит в ALREADY_ACCRUED_ENTRY_TYPES), поэтому начисление проходит
    # заново; новый год (с 20.03) при возврате закрывается теми же зеркалами.
    ctx = await _hired(db_session, OLD_HIRE)
    await _accrue(db_session, ctx, AS_OF)
    await _shift(db_session, ctx, OLD_HIRE, D(2026, 3, 20))

    await _shift(db_session, ctx, D(2026, 3, 20), OLD_HIRE)

    await _assert_total_matches(db_session, ctx, OLD_HIRE, OLD_HIRE)
    for day in _days(D(2026, 3, 20), AS_OF):
        stray = await get_balance(
            db_session, ctx.employee_id, ctx.leave_type_id, D(2026, 3, 20), as_of=day
        )
        assert stray == 0, f"остаток года с 20.03 на {day}"


@pytest.mark.parametrize("new_hire", [D(2026, 3, 20), D(2025, 9, 10)])
async def test_br_8_a_terminated_employee_total_matches_the_accrual_stopped_at_termination(
    db_session, new_hire
) -> None:
    termination = D(2026, 7, 10)
    ctx = await _hired(db_session, OLD_HIRE)
    employee = await db_session.get(Employee, ctx.employee_id)
    employee.termination_date = termination
    await db_session.flush()
    await _accrue_all(db_session, ctx, AS_OF)  # встанет на 10.07.2026

    await _shift(db_session, ctx, OLD_HIRE, new_hire)

    first = min(OLD_HIRE, new_hire)
    await _assert_total_matches(db_session, ctx, new_hire, first, stop=termination)
    late = [
        e
        for e in await _events(db_session, ctx)
        if e.entry_type == RECALCULATION and e.entry_date > termination
    ]
    assert late == []  # закрытие датировано днями прежних шагов, ни один не позже увольнения


async def test_known_boundary_a_year_closed_in_one_piece_before_t_m2_32_is_not_closed_again(
    db_session,
) -> None:
    """ЗАКРЕПЛЯЕТ ИЗВЕСТНУЮ ГРАНИЦУ, а не требует её исправления (`T-M2-32`, отметка в
    `docs/tasks.md`). Год, закрытый ОДНИМ куском на день пересчёта (так писал код до задачи),
    при возврате даты и повторном сдвиге не доводится до нуля: сумма его начислений и закрытий
    равна нулю, закрывать нечего. Тест краснеет, если такое закрытие вдруг начнёт
    дозакрываться, и тогда его надо переписать вместе с границей, а не подгонять код под него.
    """
    ctx = await _hired(db_session, OLD_HIRE)
    await _accrue(db_session, ctx, AS_OF)
    new_hire = D(2026, 3, 20)
    db_session.add(  # закрытие старого вида: один кусок, датированный днём пересчёта
        LeaveBalanceEntry(
            employee_id=ctx.employee_id,
            leave_type_id=ctx.leave_type_id,
            working_year_start=OLD_HIRE,
            working_year_end=D(2027, 3, 10),
            entry_type=RECALCULATION,
            quantity=Decimal("-18.67"),
            entry_date=AS_OF,
            reason="Пересчёт при изменении даты приёма (закрытие до T-M2-32)",
            actor_account_id=ctx.actor_id,
        )
    )
    await db_session.flush()
    await _shift(db_session, ctx, OLD_HIRE, new_hire)
    await _shift(db_session, ctx, new_hire, OLD_HIRE)  # возврат даты

    # После возврата год начислен ЗАНОВО поверх старых шагов (сумма была ноль): до дня
    # старого закрытия остаток года вдвое больше нормы-на-дату, максимум 32,66.
    after_return = [
        await get_balance(db_session, ctx.employee_id, ctx.leave_type_id, OLD_HIRE, as_of=day)
        for day in _days(OLD_HIRE, AS_OF)
    ]
    assert sum(1 for balance in after_return if balance != 0) == 215
    assert max(after_return) == Decimal("32.66")

    await _shift(db_session, ctx, OLD_HIRE, new_hire)  # повторный сдвиг

    nonzero: dict[datetime.date, Decimal] = {}
    diverged = 0
    for day in _days(OLD_HIRE, AS_OF):
        balance = await get_balance(
            db_session, ctx.employee_id, ctx.leave_type_id, OLD_HIRE, as_of=day
        )
        if balance != 0:
            nonzero[day] = balance
        total = await get_total_balance(db_session, ctx.employee_id, ctx.leave_type_id, as_of=day)
        if total != _expected_total(new_hire, day):
            diverged += 1
    assert (len(nonzero), set(nonzero.values()), diverged) == (
        92,
        {Decimal("4.67"), Decimal("9.33"), Decimal("13.99")},
        92,
    )
    assert min(nonzero) == D(2026, 7, 24)
