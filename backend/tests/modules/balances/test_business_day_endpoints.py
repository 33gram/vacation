"""Бизнес-день в слое API остатка (T-M2-34, `NFR-LOC-002`, `BR-12`).

Момент задан: 2026-11-12T22:00Z - в Москве уже 13.11 (01:00), по UTC ещё 12.11. Каждый тест
проверяет, что день берётся по ПОЯСУ ОРГАНИЗАЦИИ (параметр), а не по UTC: при сидированной
Москве - 13.11, при подставленном в параметр `UTC` - 12.11, момент один и тот же."""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import select, text

from app.models.employee import Employee
from app.models.leave_balance_entry import ACCRUAL, MANUAL_ADJUSTMENT, LeaveBalanceEntry
from app.modules.balances.accrual import accrual_steps
from app.modules.business_rules import business_day
from tests.modules.auth.factories import bearer_header, make_account, make_employee
from tests.modules.balances.helpers import make_context

MOMENT = datetime.datetime(2026, 11, 12, 22, 0, tzinfo=datetime.UTC)
MOSCOW_DAY = datetime.date(2026, 11, 13)
UTC_DAY = datetime.date(2026, 11, 12)


@pytest.fixture(autouse=True)
def _frozen_business_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(business_day, "utcnow", lambda: MOMENT)


async def _use_zone(db_session, zone: str) -> None:
    await db_session.execute(
        text("UPDATE business_rules_config SET value = :zone WHERE key = 'business_timezone'"),
        {"zone": zone},
    )


def _steps(hire: datetime.date, as_of: datetime.date) -> int:
    return len(
        list(
            accrual_steps(
                hire_date=hire,
                working_year_start=hire,
                as_of=as_of,
                annual_norm=28,
                rounding_threshold_days=15,
                excluded_days=0,
            )
        )
    )


def _hire_with_a_step_on_the_moscow_day() -> datetime.date:
    """Дата приёма, при которой шаг начисления приходится ровно на 13.11: до 13.11 шагов на
    один больше, чем до 12.11. Найдена перебором по расчёту, а не подогнана числом."""
    for back in range(1, 60):
        hire = MOSCOW_DAY - datetime.timedelta(days=back)
        # До 12.11 шаг уже есть: иначе по UTC событий нет вовсе, а проверка «не позже» пуста.
        if _steps(hire, UTC_DAY) >= 1 and _steps(hire, MOSCOW_DAY) == _steps(hire, UTC_DAY) + 1:
            return hire
    raise AssertionError("no hire date gives a step on the Moscow day")


async def _employee(db_session, hire: datetime.date):
    ctx = await make_context(db_session, "bd")
    employee = await db_session.get(Employee, ctx.employee_id)
    employee.hire_date = hire
    employee.created_at = datetime.datetime(hire.year, hire.month, hire.day, tzinfo=datetime.UTC)
    own = await make_account(db_session, employee_id=employee.id, role="employee", auth_type="ldap")
    await db_session.flush()
    return ctx, employee, own


async def _hr(db_session):
    holder = await make_employee(db_session, email="hr-bd@company.local")
    return await make_account(db_session, employee_id=holder.id, role="hr_admin", auth_type="local")


async def _events(db_session, employee_id: int, entry_type: str) -> list[LeaveBalanceEntry]:
    result = await db_session.execute(
        select(LeaveBalanceEntry)
        .where(LeaveBalanceEntry.employee_id == employee_id)
        .where(LeaveBalanceEntry.entry_type == entry_type)
        .order_by(LeaveBalanceEntry.id)
    )
    return list(result.scalars())


def _url(employee_id: int, suffix: str = "") -> str:
    return f"/api/employees/{employee_id}/balances{suffix}"


async def test_the_answer_names_the_server_business_day_and_the_default_date_is_it(
    db_session, api_client
) -> None:
    ctx, employee, own = await _employee(db_session, MOSCOW_DAY - datetime.timedelta(days=200))

    moscow = await api_client.get(_url(employee.id), headers=bearer_header(own))
    await _use_zone(db_session, "UTC")
    utc = await api_client.get(_url(employee.id), headers=bearer_header(own))

    assert (moscow.json()["today"], moscow.json()["as_of"]) == (str(MOSCOW_DAY), str(MOSCOW_DAY))
    assert (utc.json()["today"], utc.json()["as_of"]) == (str(UTC_DAY), str(UTC_DAY))


async def test_today_stays_the_business_day_when_another_date_is_requested(
    db_session, api_client
) -> None:
    ctx, employee, own = await _employee(db_session, MOSCOW_DAY - datetime.timedelta(days=200))

    response = await api_client.get(
        _url(employee.id), params={"as_of": "2026-10-01"}, headers=bearer_header(own)
    )

    assert response.json()["as_of"] == "2026-10-01"
    assert response.json()["today"] == str(MOSCOW_DAY)  # «сегодня» не равно запрошенной дате


async def test_the_accrual_runs_up_to_the_business_day_not_the_utc_day(
    db_session, api_client
) -> None:
    hire = _hire_with_a_step_on_the_moscow_day()
    ctx, employee, own = await _employee(db_session, hire)

    await api_client.get(_url(employee.id), headers=bearer_header(own))

    written = await _events(db_session, employee.id, ACCRUAL)
    assert len(written) == _steps(hire, MOSCOW_DAY)  # шаг 13.11 записан
    assert max(event.entry_date for event in written) == MOSCOW_DAY
    assert len(written) == _steps(hire, UTC_DAY) + 1


async def test_with_the_utc_zone_the_same_moment_accrues_only_up_to_the_utc_day(
    db_session, api_client
) -> None:
    hire = _hire_with_a_step_on_the_moscow_day()
    ctx, employee, own = await _employee(db_session, hire)
    await _use_zone(db_session, "UTC")

    await api_client.get(_url(employee.id), headers=bearer_header(own))

    written = await _events(db_session, employee.id, ACCRUAL)
    assert len(written) == _steps(hire, UTC_DAY)
    assert max(event.entry_date for event in written) < MOSCOW_DAY


async def test_an_adjustment_dated_the_moscow_day_is_accepted_and_the_next_day_refused(
    db_session, api_client
) -> None:
    ctx, employee, own = await _employee(db_session, MOSCOW_DAY - datetime.timedelta(days=200))
    hr = await _hr(db_session)

    def body(entry_date: datetime.date) -> dict[str, str]:
        return {
            "leave_type_id": ctx.leave_type_id,
            "quantity": "1",
            "reason": "Причина",
            "entry_date": str(entry_date),
        }

    accepted = await api_client.post(
        _url(employee.id, "/adjust"), json=body(MOSCOW_DAY), headers=bearer_header(hr)
    )
    refused = await api_client.post(
        _url(employee.id, "/adjust"),
        json=body(MOSCOW_DAY + datetime.timedelta(days=1)),
        headers=bearer_header(hr),
    )

    assert accepted.status_code == 200  # 13.11: по Москве это сегодня (по UTC было бы будущее)
    assert refused.status_code == 400
    assert refused.json()["detail"] == "entry_date: Дата корректировки не может быть в будущем."
    manual = await _events(db_session, employee.id, MANUAL_ADJUSTMENT)
    assert [event.entry_date for event in manual] == [MOSCOW_DAY]


async def test_with_the_utc_zone_the_moscow_day_is_a_future_date_for_an_adjustment(
    db_session, api_client
) -> None:
    ctx, employee, own = await _employee(db_session, MOSCOW_DAY - datetime.timedelta(days=200))
    hr = await _hr(db_session)
    await _use_zone(db_session, "UTC")

    response = await api_client.post(
        _url(employee.id, "/adjust"),
        json={
            "leave_type_id": ctx.leave_type_id,
            "quantity": "1",
            "reason": "Причина",
            "entry_date": str(MOSCOW_DAY),
        },
        headers=bearer_header(hr),
    )

    assert response.status_code == 400  # тот же момент, иной пояс - иное «сегодня»
    assert await _events(db_session, employee.id, MANUAL_ADJUSTMENT) == []


async def test_an_adjustment_without_a_date_is_dated_the_business_day(
    db_session, api_client
) -> None:
    ctx, employee, own = await _employee(db_session, MOSCOW_DAY - datetime.timedelta(days=200))
    hr = await _hr(db_session)

    response = await api_client.post(
        _url(employee.id, "/adjust"),
        json={"leave_type_id": ctx.leave_type_id, "quantity": "1", "reason": "Причина"},
        headers=bearer_header(hr),
    )

    assert response.status_code == 200
    (event,) = await _events(db_session, employee.id, MANUAL_ADJUSTMENT)
    assert event.entry_date == MOSCOW_DAY
