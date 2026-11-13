"""Барьер против выдуманного остатка и ленивое начисление при чтении
(T-M2-19, `FR-BAL-001`, `FR-BAL-002`, `BR-8`).

Ожидаемые числа посчитаны на бумаге: приём 10.03.2026, расчёт на 24.10.2026 даёт
18,67 (7 полных месяцев + остаток 15 дней при норме 28).
"""

from __future__ import annotations

import datetime
import zoneinfo
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models.employee import Employee
from app.models.leave_balance_entry import INITIAL_IMPORT, LeaveBalanceEntry
from app.modules.balances.service import (
    BalanceEntryError,
    LeaveTypeNotSeededError,
    accrue_all_working_years,
    accrue_before_read,
    get_total_balance,
)
from tests.modules.balances.helpers import Context, make_context

D = datetime.date
AS_OF = D(2026, 10, 24)


async def _employee(
    db_session, hire: datetime.date, card_created: datetime.datetime, suffix: str = "a"
) -> Context:
    ctx = await make_context(db_session, suffix)
    employee = await db_session.get(Employee, ctx.employee_id)
    employee.hire_date = hire
    employee.created_at = card_created
    await db_session.flush()
    return ctx


def _utc(year: int, month: int, day: int, hour: int = 12) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, tzinfo=datetime.UTC)


async def _catch_up(db_session, ctx: Context, as_of: datetime.date = AS_OF):
    return await accrue_all_working_years(
        db_session, employee_id=ctx.employee_id, leave_type_id=ctx.leave_type_id, as_of=as_of
    )


async def _count(db_session, ctx: Context) -> int:
    result = await db_session.execute(
        select(func.count())
        .select_from(LeaveBalanceEntry)
        .where(LeaveBalanceEntry.employee_id == ctx.employee_id)
    )
    return result.scalar_one()


# --- барьер: годы, закончившиеся до появления карточки -----------------------


async def test_barrier_a_2015_hire_with_a_card_created_now_accrues_only_the_current_year(
    db_session,
) -> None:
    ctx = await _employee(db_session, D(2015, 3, 10), _utc(2026, 10, 24))

    result = await _catch_up(db_session, ctx)

    assert result.accrued == {D(2026, 3, 10): Decimal("18.67")}
    assert set(result.skipped) == {D(year, 3, 10) for year in range(2015, 2026)}
    assert all("до появления карточки" in reason for reason in result.skipped.values())
    assert await _count(db_session, ctx) == 8  # T-M2-21: по событию на шаг, 24.03-24.10


async def test_barrier_a_card_created_a_week_after_the_hire_keeps_the_first_year_whole(
    db_session,
) -> None:
    # Приём 10.03.2025, карточка 17.03.2025: первый год закончился ПОСЛЕ появления
    # карточки, значит начисляется целиком (28,00); «завели карточку через неделю» не
    # стоит сотруднику года.
    ctx = await _employee(db_session, D(2025, 3, 10), _utc(2025, 3, 17))

    result = await _catch_up(db_session, ctx)

    assert result.accrued[D(2025, 3, 10)] == Decimal("28.00")
    assert result.skipped == {}


async def test_barrier_a_card_created_on_the_last_day_of_a_year_keeps_that_year(
    db_session,
) -> None:
    # Год с 10.03.2025 кончается 09.03.2026 включительно.
    ctx = await _employee(db_session, D(2025, 3, 10), _utc(2026, 3, 9))

    result = await _catch_up(db_session, ctx)

    assert D(2025, 3, 10) in result.accrued
    assert result.skipped == {}


async def test_barrier_a_card_created_on_the_first_day_of_the_next_year_drops_the_old_year(
    db_session,
) -> None:
    ctx = await _employee(db_session, D(2025, 3, 10), _utc(2026, 3, 10))

    result = await _catch_up(db_session, ctx)

    assert set(result.skipped) == {D(2025, 3, 10)}
    assert D(2026, 3, 10) in result.accrued


async def test_barrier_the_card_date_is_the_day_of_the_organization_not_the_utc_day(
    db_session,
) -> None:
    # T-M2-40 (ПЕРЕПИСАНО; прежняя редакция называлась `..._is_taken_in_utc_not_in_the_local_zone`
    # и ожидала обратное). 10.03.2026 01:00 по Москве - это 09.03.2026 22:00 UTC. «Год закончился
    # ДО появления карточки» - утверждение о дне ОРГАНИЗАЦИИ (`NFR-LOC-002`): карточка появилась
    # 10.03, год 10.03.2025-09.03.2026 закончился 09.03 - до неё, поэтому он ПРОПУСКАЕТСЯ
    # (прошлое приходит начальным остатком), а год с 10.03.2026 вычисляется. Момент, пояс и
    # карточка те же, что были; меняется только ожидаемый исход.
    moscow = datetime.timezone(datetime.timedelta(hours=3))
    created = datetime.datetime(2026, 3, 10, 1, 0, tzinfo=moscow)
    ctx = await _employee(db_session, D(2025, 3, 10), created)

    result = await _catch_up(db_session, ctx)

    assert set(result.skipped) == {D(2025, 3, 10)}
    assert D(2026, 3, 10) in result.accrued


async def _set_zone(db_session, zone: str) -> None:
    await db_session.execute(
        text("UPDATE business_rules_config SET value = :zone WHERE key = 'business_timezone'"),
        {"zone": zone},
    )


# Одна и та же метка 2026-03-09T22:00Z: по Москве (UTC+3) и Окленду (UTC+13, летнее время до апреля)
# это уже 10.03.2026, по UTC и Нью-Йорку (EDT с 08.03, UTC-4, 18:00) - ещё 09.03.
@pytest.mark.parametrize(
    ("zone", "year_is_skipped"),
    [
        ("Europe/Moscow", True),
        ("Pacific/Auckland", True),
        ("UTC", False),
        ("America/New_York", False),
    ],
)
async def test_barrier_the_outcome_follows_the_zone_parameter_on_one_and_the_same_stamp(
    db_session, zone: str, year_is_skipped: bool
) -> None:
    ctx = await _employee(db_session, D(2025, 3, 10), _utc(2026, 3, 9, 22))
    await _set_zone(db_session, zone)

    result = await _catch_up(db_session, ctx)

    assert (D(2025, 3, 10) in result.skipped) is year_is_skipped
    assert (D(2025, 3, 10) in result.accrued) is not year_is_skipped


async def test_barrier_the_reason_names_the_card_date_of_the_organization(db_session) -> None:
    ctx = await _employee(db_session, D(2025, 3, 10), _utc(2026, 3, 9, 22))

    result = await _catch_up(db_session, ctx)

    reason = result.skipped[D(2025, 3, 10)]
    # Метка 09.03 22:00 UTC показана днём организации (Москва) - 10.03.2026, не UTC-днём 09.03.
    assert "(10.03.2026)" in reason
    assert "(09.03.2026)" not in reason
    assert "закончился до появления карточки" in reason


async def test_barrier_a_year_computed_before_a_change_of_the_zone_keeps_its_events(
    db_session,
) -> None:
    # Граница T-M2-40: журнал добавляемый, задним числом не переписывается. Год вычислен при
    # поясе UTC (карточка 09.03 по UTC), затем пояс сменили на Москву, где этот год уже
    # «закончился до появления карточки». ПРОВЕРЯЕТСЯ ПРОГОНОМ ИТОГ: события остаются, их число и
    # общий остаток прежние, год назван пропущенным. ИДЕМПОТЕНТНОСТЬ `accrue_leave` здесь НЕ
    # исполняется: барьер не доходит до его вызова, и отсутствие повторной записи следует из
    # чтения кода. Сама идемпотентность проверена своими тестами:
    # `test_accrual_service.py::test_fr_bal_001_a_repeated_call_for_the_same_date_writes_nothing` и
    # `test_accrual_step_dates.py::test_a_repeated_call_writes_nothing_and_a_call_a_year_later_...`.
    ctx = await _employee(db_session, D(2025, 3, 10), _utc(2026, 3, 9, 22))
    await _set_zone(db_session, "UTC")
    first = await _catch_up(db_session, ctx)
    assert D(2025, 3, 10) in first.accrued
    events_before = await _count(db_session, ctx)
    total_before = await get_total_balance(
        db_session, ctx.employee_id, ctx.leave_type_id, as_of=AS_OF
    )

    await _set_zone(db_session, "Europe/Moscow")
    second = await _catch_up(db_session, ctx)

    assert D(2025, 3, 10) in second.skipped and D(2025, 3, 10) not in second.accrued
    assert await _count(db_session, ctx) == events_before
    total_after = await get_total_balance(
        db_session, ctx.employee_id, ctx.leave_type_id, as_of=AS_OF
    )
    assert total_after == total_before


async def test_barrier_a_year_under_both_rules_names_both_reasons(db_session) -> None:
    ctx = await _employee(db_session, D(2023, 3, 10), _utc(2026, 10, 24))
    db_session.add(
        LeaveBalanceEntry(
            employee_id=ctx.employee_id,
            leave_type_id=ctx.leave_type_id,
            working_year_start=D(2025, 3, 10),
            working_year_end=D(2026, 3, 10),
            entry_type=INITIAL_IMPORT,
            quantity=Decimal("5"),
            entry_date=D(2026, 10, 24),
            actor_account_id=ctx.actor_id,
        )
    )
    await db_session.flush()

    result = await _catch_up(db_session, ctx)

    reason = result.skipped[D(2025, 3, 10)]
    assert "до появления карточки" in reason and "начальный остаток" in reason


async def test_barrier_an_import_closes_years_as_before_when_the_card_is_old(db_session) -> None:
    ctx = await _employee(db_session, D(2023, 3, 10), _utc(2023, 3, 10))
    db_session.add(
        LeaveBalanceEntry(
            employee_id=ctx.employee_id,
            leave_type_id=ctx.leave_type_id,
            working_year_start=D(2025, 3, 10),
            working_year_end=D(2026, 3, 10),
            entry_type=INITIAL_IMPORT,
            quantity=Decimal("5"),
            entry_date=D(2026, 1, 1),
            actor_account_id=ctx.actor_id,
        )
    )
    await db_session.flush()

    result = await _catch_up(db_session, ctx)

    assert set(result.skipped) == {D(2023, 3, 10), D(2024, 3, 10), D(2025, 3, 10)}
    assert all("начальный остаток" in reason for reason in result.skipped.values())
    assert result.accrued == {D(2026, 3, 10): Decimal("18.67")}


# --- ленивое начисление при чтении --------------------------------------------


async def test_lazy_the_first_read_writes_the_accrual_and_the_second_writes_nothing(
    db_session,
) -> None:
    ctx = await _employee(db_session, D(2023, 3, 10), _utc(2023, 3, 10))

    first = await accrue_before_read(db_session, employee_id=ctx.employee_id, as_of=AS_OF)
    after_first = await _count(db_session, ctx)
    second = await accrue_before_read(db_session, employee_id=ctx.employee_id, as_of=AS_OF)

    # T-M2-21: три завершённых года по 12 шагов и восемь шагов текущего
    assert after_first == 3 * 12 + 8 and first.accrued
    assert second.accrued == {}
    assert await _count(db_session, ctx) == after_first  # повтор безопасен
    total = await get_total_balance(db_session, ctx.employee_id, ctx.leave_type_id)
    assert total == Decimal("102.67")  # 28 + 28 + 28 + 18,67


async def test_lazy_a_read_on_a_past_date_does_not_accrue_forward(db_session) -> None:
    ctx = await _employee(db_session, D(2023, 3, 10), _utc(2023, 3, 10))

    await accrue_before_read(db_session, employee_id=ctx.employee_id, as_of=D(2024, 10, 24))

    years = (
        await db_session.execute(
            select(LeaveBalanceEntry.working_year_start).where(
                LeaveBalanceEntry.employee_id == ctx.employee_id
            )
        )
    ).scalars()
    assert sorted(set(years)) == [D(2023, 3, 10), D(2024, 3, 10)]  # 2025 и 2026 не тронуты


async def test_lazy_an_employee_whose_every_year_is_closed_reads_without_an_error(
    db_session,
) -> None:
    ctx = await _employee(db_session, D(2026, 3, 10), _utc(2026, 3, 10))
    db_session.add(
        LeaveBalanceEntry(
            employee_id=ctx.employee_id,
            leave_type_id=ctx.leave_type_id,
            working_year_start=D(2026, 3, 10),
            working_year_end=D(2027, 3, 10),
            entry_type=INITIAL_IMPORT,
            quantity=Decimal("5"),
            entry_date=D(2026, 4, 1),
            actor_account_id=ctx.actor_id,
        )
    )
    await db_session.flush()

    result = await accrue_before_read(db_session, employee_id=ctx.employee_id, as_of=AS_OF)

    assert result.accrued == {} and set(result.skipped) == {D(2026, 3, 10)}


async def test_lazy_a_failed_accrual_is_loud_not_swallowed(db_session) -> None:
    with pytest.raises(BalanceEntryError):
        await accrue_before_read(db_session, employee_id=10_000_000, as_of=AS_OF)


async def test_lazy_a_missing_main_leave_type_is_a_configuration_error(db_session) -> None:
    ctx = await _employee(db_session, D(2026, 3, 10), _utc(2026, 3, 10))
    await db_session.execute(text("DELETE FROM leave_types WHERE code = 'annual_paid'"))

    with pytest.raises(LeaveTypeNotSeededError):
        await accrue_before_read(db_session, employee_id=ctx.employee_id, as_of=AS_OF)


# --- уволенный сотрудник ---------------------------------------------------------


async def test_terminated_accrual_stops_at_the_termination_date_even_a_year_later(
    db_session,
) -> None:
    # Принят 10.03.2026, уволен 10.07.2026 (последний день работы - включительно).
    # Прочитан спустя год (27.10.2027): событий после даты увольнения нет.
    ctx = await _employee(db_session, D(2026, 3, 10), _utc(2026, 3, 10))
    employee = await db_session.get(Employee, ctx.employee_id)
    employee.employment_status = "terminated"
    employee.termination_date = D(2026, 7, 10)
    await db_session.flush()

    result = await _catch_up(db_session, ctx, as_of=D(2027, 10, 27))

    # 4 полных месяца (10.03-09.07) + остаток 10.07 = 1 день < 15: 28 * 4 / 12.
    assert result.accrued == {D(2026, 3, 10): Decimal("9.33")}
    entries = (
        await db_session.execute(
            select(LeaveBalanceEntry.entry_date).where(
                LeaveBalanceEntry.employee_id == ctx.employee_id
            )
        )
    ).scalars()
    # последний шаг - четвёртый месяц (24.06): после даты увольнения событий нет
    assert max(entries) == D(2026, 6, 24)
    again = await _catch_up(db_session, ctx, as_of=D(2028, 10, 27))
    assert again.accrued == {}  # и ещё через год - ничего нового


async def test_terminated_without_a_termination_date_is_a_loud_refusal(db_session) -> None:
    ctx = await _employee(db_session, D(2026, 3, 10), _utc(2026, 3, 10))
    employee = await db_session.get(Employee, ctx.employee_id)
    employee.employment_status = "terminated"
    employee.termination_date = None
    await db_session.flush()

    with pytest.raises(BalanceEntryError, match="не указана дата увольнения"):
        await _catch_up(db_session, ctx)

    assert await _count(db_session, ctx) == 0


async def test_barrier_an_explicit_zone_is_used_and_the_parameter_is_not_read_again(
    db_session,
) -> None:
    # PR #198 review (П-5): вызывающий, который уже знает пояс организации, передаёт его - внутри
    # ОДНОГО запроса «сегодня» и дата карточки не могут оказаться из разных поясов. Параметр в базе
    # - UTC (год вычислялся бы), переданный пояс - Москва: исход следует за ПЕРЕДАННЫМ поясом.
    ctx = await _employee(db_session, D(2025, 3, 10), _utc(2026, 3, 9, 22))
    await _set_zone(db_session, "UTC")

    result = await accrue_all_working_years(
        db_session,
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        as_of=AS_OF,
        zone=zoneinfo.ZoneInfo("Europe/Moscow"),
    )

    assert D(2025, 3, 10) in result.skipped
