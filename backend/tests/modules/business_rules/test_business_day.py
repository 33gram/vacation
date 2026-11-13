"""Бизнес-день (T-M2-34, `NFR-LOC-002`): один момент - разные сутки в разных поясах, перевод по
правилам зоны (не сдвигом на фиксированные часы), независимость от пояса процесса."""

from __future__ import annotations

import datetime
import time
import zoneinfo

import pytest
from sqlalchemy import text

from app.core.time_utils import business_date
from app.modules.business_rules import business_day
from app.modules.business_rules.business_day import business_today
from app.modules.business_rules.service import BUSINESS_TIMEZONE, BusinessRuleError

MOSCOW = zoneinfo.ZoneInfo("Europe/Moscow")
UTC = zoneinfo.ZoneInfo("UTC")


def _utc(*args: int) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=datetime.UTC)


def test_the_same_moment_is_two_different_days_in_moscow_and_in_utc() -> None:
    moment = _utc(2026, 11, 12, 22, 0)

    assert business_date(moment, MOSCOW) == datetime.date(2026, 11, 13)
    assert business_date(moment, UTC) == datetime.date(2026, 11, 12)


def test_the_boundary_is_the_zone_midnight_not_the_utc_one() -> None:
    assert business_date(_utc(2026, 11, 12, 20, 59), MOSCOW) == datetime.date(2026, 11, 12)
    assert business_date(_utc(2026, 11, 12, 21, 0), MOSCOW) == datetime.date(2026, 11, 13)


@pytest.mark.parametrize(
    ("zone", "summer", "winter"),
    [
        # Один и тот же час UTC: летом смещение больше на час, и дата иная. Фиксированный сдвиг
        # (любой один) дал бы одну и ту же дату в обоих случаях.
        ("America/New_York", _utc(2026, 7, 1, 4, 30), _utc(2026, 1, 1, 4, 30)),
        ("Europe/Berlin", _utc(2026, 6, 30, 22, 30), _utc(2026, 12, 31, 22, 30)),
    ],
)
def test_a_zone_with_daylight_saving_is_not_a_fixed_shift(
    zone: str, summer: datetime.datetime, winter: datetime.datetime
) -> None:
    info = zoneinfo.ZoneInfo(zone)

    summer_date = business_date(summer, info)
    winter_date = business_date(winter, info)

    # Одинаковое UTC-время суток (04:30 / 22:30): при фиксированном сдвиге разница даты и UTC
    # была бы одной и той же; здесь она разная.
    assert (summer_date - summer.date()) != (winter_date - winter.date())


def test_the_new_york_dates_around_the_spring_transition() -> None:
    new_york = zoneinfo.ZoneInfo("America/New_York")
    # Переход 2026-03-08 в 07:00 UTC (02:00 EST -> 03:00 EDT).
    assert business_date(_utc(2026, 3, 8, 4, 59), new_york) == datetime.date(2026, 3, 7)  # EST
    assert business_date(_utc(2026, 3, 9, 3, 59), new_york) == datetime.date(2026, 3, 8)  # EDT
    assert business_date(_utc(2026, 3, 9, 4, 0), new_york) == datetime.date(2026, 3, 9)  # EDT


def test_a_naive_moment_is_refused_not_read_as_the_local_time_of_the_process() -> None:
    with pytest.raises(ValueError):
        business_date(datetime.datetime(2026, 11, 12, 22, 0), MOSCOW)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    def _set(moment: datetime.datetime) -> None:
        monkeypatch.setattr(business_day, "utcnow", lambda: moment)

    return _set


async def _set_zone(db_session, value: str) -> None:
    await db_session.execute(
        text("UPDATE business_rules_config SET value = :value WHERE key = 'business_timezone'"),
        {"value": value},
    )


async def test_business_today_follows_the_parameter_for_one_moment(db_session, clock) -> None:
    clock(_utc(2026, 11, 12, 22, 0))

    assert await business_today(db_session) == datetime.date(2026, 11, 13)  # сидирование: Москва
    await _set_zone(db_session, "UTC")
    assert await business_today(db_session) == datetime.date(2026, 11, 12)


async def test_business_today_does_not_depend_on_the_time_zone_of_the_process(
    db_session, clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock(_utc(2026, 11, 12, 22, 0))
    try:
        for process_zone in ("UTC", "Pacific/Auckland", "America/Los_Angeles"):
            monkeypatch.setenv("TZ", process_zone)
            time.tzset()
            assert await business_today(db_session) == datetime.date(2026, 11, 13)
    finally:
        monkeypatch.undo()
        time.tzset()


async def test_business_today_without_the_key_is_refused_there_is_no_default(
    db_session, clock
) -> None:
    clock(_utc(2026, 11, 12, 22, 0))
    await db_session.execute(
        text("DELETE FROM business_rules_config WHERE key = 'business_timezone'")
    )

    with pytest.raises(BusinessRuleError) as excinfo:
        await business_today(db_session)

    assert excinfo.value.key == BUSINESS_TIMEZONE


# --- Клаузы NFR-LOC-002 (PR #196 review, П-2) ----------------------------------------------


async def _hr_actor(db_session):
    from tests.modules.auth.factories import make_account, make_employee

    holder = await make_employee(db_session, email="tz-edit@company.local")
    return await make_account(db_session, employee_id=holder.id, role="hr_admin", auth_type="local")


async def test_nfr_loc_002_an_admin_edit_takes_effect_at_the_next_call(db_session, clock) -> None:
    from app.modules.business_rules.service import update_rules

    clock(_utc(2026, 11, 12, 22, 0))
    admin = await _hr_actor(db_session)

    before = await business_today(db_session)
    await update_rules(db_session, {BUSINESS_TIMEZONE: "UTC"}, actor_account_id=admin.id)
    after = await business_today(db_session)

    # Момент зафиксирован, подменён только параметр - и именно настоящей правкой.
    assert (before, after) == (datetime.date(2026, 11, 13), datetime.date(2026, 11, 12))


async def test_nfr_loc_002_a_change_of_the_zone_does_not_rewrite_the_journal(
    db_session, clock
) -> None:
    from sqlalchemy import select

    from app.models.employee import Employee
    from app.models.leave_balance_entry import LeaveBalanceEntry
    from app.modules.balances.service import accrue_before_read
    from app.modules.business_rules.service import update_rules
    from tests.modules.balances.helpers import make_context

    clock(_utc(2026, 11, 12, 22, 0))
    ctx = await make_context(db_session, "tzj")
    employee = await db_session.get(Employee, ctx.employee_id)
    employee.hire_date = datetime.date(2026, 4, 1)
    employee.created_at = _utc(2026, 4, 1, 0, 0)
    await db_session.flush()
    admin = await _hr_actor(db_session)

    async def journal() -> list[tuple]:
        rows = await db_session.execute(
            select(
                LeaveBalanceEntry.id,
                LeaveBalanceEntry.entry_type,
                LeaveBalanceEntry.entry_date,
                LeaveBalanceEntry.quantity,
            )
            .where(LeaveBalanceEntry.employee_id == employee.id)
            .order_by(LeaveBalanceEntry.id)
        )
        return [tuple(row) for row in rows]

    # Начисление до бизнес-дня ПО МОСКВЕ (13.11), затем пояс меняется на тот, где этот день - 12.11.
    await accrue_before_read(
        db_session, employee_id=employee.id, as_of=await business_today(db_session)
    )
    written = await journal()
    assert written  # предусловие: события есть
    await update_rules(db_session, {BUSINESS_TIMEZONE: "UTC"}, actor_account_id=admin.id)
    await accrue_before_read(
        db_session, employee_id=employee.id, as_of=await business_today(db_session)
    )

    # Ни одной удалённой, переписанной или добавленной строки, ни одного `recalculation`.
    assert await journal() == written
    assert all(row[1] != "recalculation" for row in await journal())
