"""Критерий приёмки T-M2-33 (`FR-BAL-002/2`, `BR-8`): на дату позже бизнес-дня `GET .../balances`
показывает ПРОГНОЗ - записанное плюс начисление без записи - с признаком `is_forecast`, и журнал
при этом не меняется.

Момент задан (как в `test_business_day_endpoints.py`): 2026-11-12T22:00Z, по Москве бизнес-день
13.11.2026, по UTC - 12.11. Сотрудник принят за 200 дней до бизнес-дня (27.04.2026), норма 28,
порог 15.

Сумма по годам проверяется НЕ промышленным кодом: `expected_total_accrual` (helpers.py) считает по
`compute_accrual` с явными началами лет и не вызывает ни `forecast_accrual`, ни `accrue_leave`
(дубль намеренный, см. её докстринг); кроме неё - ЖЁСТКИЕ числа, измеренные прогоном.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models.employee import Employee
from app.models.leave_balance_entry import LeaveBalanceEntry
from app.modules.balances.accrual import compute_accrual
from app.modules.balances.service import accrue_all_working_years, adjust_balance
from app.modules.business_rules import business_day
from tests.modules.auth.factories import bearer_header, make_account, make_employee
from tests.modules.balances.helpers import expected_total_accrual, make_context

MOMENT = datetime.datetime(2026, 11, 12, 22, 0, tzinfo=datetime.UTC)
TODAY = datetime.date(2026, 11, 13)  # бизнес-день по Москве
HIRE = TODAY - datetime.timedelta(days=200)  # 27.04.2026
DAY = datetime.timedelta(days=1)


@pytest.fixture(autouse=True)
def _frozen_business_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(business_day, "utcnow", lambda: MOMENT)


async def _employee(db_session, hire: datetime.date = HIRE, **fields):
    ctx = await make_context(db_session, "fc")
    employee = await db_session.get(Employee, ctx.employee_id)
    employee.hire_date = hire
    # Карточка заведена давно: барьер появления карточки (`T-M2-19`) прошлых лет не отсекает.
    employee.created_at = datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
    for name, value in fields.items():
        setattr(employee, name, value)
    own = await make_account(db_session, employee_id=employee.id, role="employee", auth_type="ldap")
    await db_session.flush()
    return ctx, employee, own


async def _get(api_client, employee, own, as_of: datetime.date | None = None):
    params = {} if as_of is None else {"as_of": str(as_of)}
    return await api_client.get(
        f"/api/employees/{employee.id}/balances", params=params, headers=bearer_header(own)
    )


def _main(response) -> dict:
    return next(b for b in response.json()["balances"] if b["code"] == "annual_paid")


async def _event_count(db_session, employee_id: int) -> int:
    return await db_session.scalar(
        select(func.count())
        .select_from(LeaveBalanceEntry)
        .where(LeaveBalanceEntry.employee_id == employee_id)
    )


async def test_the_boundary_the_business_day_is_recorded_the_next_day_is_a_forecast(
    db_session, api_client
) -> None:
    ctx, employee, own = await _employee(db_session)

    on_day = await _get(api_client, employee, own, TODAY)
    next_day = await _get(api_client, employee, own, TODAY + DAY)

    assert on_day.status_code == next_day.status_code == 200
    assert on_day.json()["is_forecast"] is False
    assert _main(on_day)["forecast_accrual"] is None
    assert next_day.json()["is_forecast"] is True
    assert _main(next_day)["forecast_accrual"] is not None


async def test_the_boundary_follows_the_organization_zone_parameter_not_utc(
    db_session, api_client
) -> None:
    # Тот же момент: по Москве 13.11 - сегодня, по UTC - 12.11, и 13.11 уже будущее.
    ctx, employee, own = await _employee(db_session)

    moscow = await _get(api_client, employee, own, TODAY)
    await db_session.execute(
        text("UPDATE business_rules_config SET value = 'UTC' WHERE key = 'business_timezone'")
    )
    utc = await _get(api_client, employee, own, TODAY)

    assert moscow.json()["is_forecast"] is False
    assert utc.json()["is_forecast"] is True


async def test_a_forecast_inside_the_current_working_year_equals_the_accrual_on_that_date(
    db_session, api_client
) -> None:
    ctx, employee, own = await _employee(db_session)
    forecast_day = TODAY + datetime.timedelta(days=30)

    recorded = _main(await _get(api_client, employee, own))
    forecast = _main(await _get(api_client, employee, own, forecast_day))

    expected = compute_accrual(
        hire_date=HIRE,
        as_of=forecast_day,
        annual_norm=28,
        rounding_threshold_days=15,
        excluded_days=0,
    ).accrued
    assert Decimal(forecast["balance"]) == expected
    assert Decimal(forecast["total_balance"]) == expected
    # Жёсткие числа (измерено прогоном; числа задачи): записано 16,33, к 13.12.2026 - 18,67.
    assert Decimal(recorded["balance"]) == Decimal("16.33")
    assert Decimal(forecast["balance"]) == Decimal("18.67")
    assert Decimal(forecast["forecast_accrual"]) == Decimal("2.34")


async def test_a_forecast_beyond_the_working_year_is_the_sum_over_all_years(
    db_session, api_client
) -> None:
    ctx, employee, own = await _employee(db_session)
    far = TODAY + datetime.timedelta(days=400)  # 18.12.2027: второй рабочий год (с 27.04.2027)

    answer = await _get(api_client, employee, own, far)

    main = _main(answer)
    assert answer.status_code == 200
    # Независимая проверка (не промышленным кодом) и жёсткое число.
    assert Decimal(main["total_balance"]) == expected_total_accrual(HIRE, far)
    assert Decimal(main["total_balance"]) == Decimal("46.67")  # 28 за первый год + 18,67 за второй
    # Блок года - год, в который попадает дата (с 27.04.2027), с прогнозом в нём.
    assert main["working_year_start"] == "2027-04-27"
    assert Decimal(main["balance"]) == compute_accrual(
        hire_date=HIRE,
        as_of=far,
        annual_norm=28,
        rounding_threshold_days=15,
        excluded_days=0,
    ).accrued


@pytest.mark.parametrize("ahead", [1, 30, 200, 400, 1000, 1825])
async def test_the_forecast_total_equals_the_independent_sum_for_many_dates(
    db_session, api_client, ahead
) -> None:
    ctx, employee, own = await _employee(db_session)
    day = TODAY + datetime.timedelta(days=ahead)

    main = _main(await _get(api_client, employee, own, day))

    assert Decimal(main["total_balance"]) == expected_total_accrual(HIRE, day), ahead


async def test_the_forecast_does_not_write_the_journal_and_repeats_are_idempotent(
    db_session, api_client
) -> None:
    ctx, employee, own = await _employee(db_session)
    await _get(api_client, employee, own)  # ленивое начисление до бизнес-дня
    before = await _event_count(db_session, employee.id)
    recorded_before = _main(await _get(api_client, employee, own))["total_balance"]

    first = await _get(api_client, employee, own, TODAY + datetime.timedelta(days=400))
    second = await _get(api_client, employee, own, TODAY + datetime.timedelta(days=400))

    assert first.json() == second.json()
    assert await _event_count(db_session, employee.id) == before
    # Записанное на бизнес-день тоже не изменилось.
    assert _main(await _get(api_client, employee, own))["total_balance"] == recorded_before


async def test_a_forecast_on_a_fresh_employee_writes_only_the_lazy_accrual_up_to_the_business_day(
    db_session, api_client
) -> None:
    ctx, employee, own = await _employee(db_session)
    assert await _event_count(db_session, employee.id) == 0

    await _get(api_client, employee, own, TODAY + datetime.timedelta(days=400))

    written = (
        await db_session.execute(
            select(LeaveBalanceEntry.entry_date).where(
                LeaveBalanceEntry.employee_id == employee.id
            )
        )
    ).scalars().all()
    assert written  # ленивое начисление до бизнес-дня, как у обычного чтения
    assert max(written) <= TODAY  # вперёд ничего не записано


async def test_the_rest_of_the_answer_stays_recorded_on_a_forecast_date(
    db_session, api_client
) -> None:
    # Ф-2: `history` и `accrual_years` - записанное до бизнес-дня, и на дату вперёд они те же,
    # что в ответе без даты; прогноз живёт только в `balance`, `total_balance`, `forecast_accrual`.
    ctx, employee, own = await _employee(db_session)
    recorded = _main(await _get(api_client, employee, own))

    forecast = _main(await _get(api_client, employee, own, TODAY + datetime.timedelta(days=400)))

    assert forecast["history"] == recorded["history"]
    assert forecast["accrual_years"] == recorded["accrual_years"]
    assert forecast["total_balance"] != recorded["total_balance"]


async def test_a_terminated_employee_forecast_does_not_grow_past_the_termination_date(
    db_session, api_client
) -> None:
    termination = TODAY + datetime.timedelta(days=20)
    ctx, employee, _own = await _employee(
        db_session, termination_date=termination, employment_status="terminated"
    )
    # Уволенный в систему не входит (403): остаток смотрит HR.
    holder = await make_employee(db_session, email="hr-fc@company.local")
    hr = await make_account(db_session, employee_id=holder.id, role="hr_admin", auth_type="local")

    at_termination = _main(await _get(api_client, employee, hr, termination))
    later = _main(await _get(api_client, employee, hr, TODAY + datetime.timedelta(days=400)))

    assert Decimal(later["total_balance"]) == Decimal(at_termination["total_balance"])
    assert Decimal(later["total_balance"]) == expected_total_accrual(HIRE, termination)


async def test_a_forecast_date_before_the_hire_date_of_a_future_hire_is_zero(
    db_session, api_client
) -> None:
    hire = TODAY + datetime.timedelta(days=10)
    ctx, employee, own = await _employee(db_session, hire=hire)

    before_hire = await _get(api_client, employee, own, hire - DAY)
    on_hire = await _get(api_client, employee, own, hire)
    month_later = await _get(api_client, employee, own, hire + datetime.timedelta(days=40))

    assert before_hire.json()["is_forecast"] is True
    assert _main(before_hire)["working_year_start"] is None
    assert Decimal(_main(before_hire)["total_balance"]) == 0
    assert _main(on_hire)["working_year_start"] == str(hire)  # ровно в день приёма
    assert Decimal(_main(on_hire)["total_balance"]) == expected_total_accrual(hire, hire)
    assert Decimal(_main(month_later)["total_balance"]) == expected_total_accrual(
        hire, hire + datetime.timedelta(days=40)
    )
    assert Decimal(_main(month_later)["total_balance"]) > 0


async def test_a_date_before_the_hire_of_a_hired_employee_is_recorded_not_a_forecast(
    db_session, api_client
) -> None:
    ctx, employee, own = await _employee(db_session)

    answer = await _get(api_client, employee, own, HIRE - DAY)

    assert answer.json()["is_forecast"] is False
    assert _main(answer)["working_year_start"] is None
    assert _main(answer)["forecast_accrual"] is None


async def test_the_horizon_five_years_is_accepted_and_one_day_more_is_refused_without_a_write(
    db_session, api_client
) -> None:
    ctx, employee, own = await _employee(db_session)
    limit = datetime.date(TODAY.year + 5, TODAY.month, TODAY.day)

    refused = await _get(api_client, employee, own, limit + DAY)

    assert refused.status_code == 400
    assert refused.json()["detail"].startswith("as_of:")
    assert await _event_count(db_session, employee.id) == 0  # отказ ничего не пишет
    accepted = await _get(api_client, employee, own, limit)
    assert accepted.status_code == 200
    assert accepted.json()["is_forecast"] is True


async def test_the_forecast_shares_the_code_path_of_the_lazy_accrual_barrier(
    db_session
) -> None:
    # Прогноз = `accrue_all_working_years(write=False)`: то, что он называет, ровно то, что
    # запишет настоящий догон на ту же дату, включая барьер появления карточки.
    ctx, employee, own = await _employee(
        db_session,
        hire=datetime.date(2023, 5, 1),
        created_at=datetime.datetime(2025, 6, 1, tzinfo=datetime.UTC),
    )
    day = TODAY + datetime.timedelta(days=400)

    dry = await accrue_all_working_years(
        db_session,
        employee_id=employee.id,
        leave_type_id=ctx.leave_type_id,
        as_of=day,
        write=False,
    )
    assert await _event_count(db_session, employee.id) == 0
    real = await accrue_all_working_years(
        db_session, employee_id=employee.id, leave_type_id=ctx.leave_type_id, as_of=day
    )

    assert dry.accrued == real.accrued
    assert dry.skipped == real.skipped
    assert dry.skipped  # барьер сработал: годы до появления карточки пропущены


async def test_a_kind_without_accrual_keeps_recorded_numbers_and_no_forecast_value(
    db_session, api_client
) -> None:
    # PR #201 check, П-1: начисление есть только у основного вида. У второго вида с остатком
    # (`time_off`, отгул) прогноз не считался: `forecast_accrual` - `None` (числа записаны), а не
    # 0, и числа равны записанным; у основного - прогноз. `is_forecast` - свойство ответа.
    ctx, employee, own = await _employee(db_session)
    other_id = await db_session.scalar(text("SELECT id FROM leave_types WHERE code = 'time_off'"))
    await adjust_balance(
        db_session,
        employee_id=employee.id,
        leave_type_id=other_id,
        working_year_start=HIRE,
        working_year_end=datetime.date(2027, 4, 27),
        quantity=Decimal("5"),
        reason="Отгул за переработку",
        actor_account_id=ctx.actor_id,
        entry_date=TODAY,
    )
    recorded = await _get(api_client, employee, own)
    before = await _event_count(db_session, employee.id)
    far = TODAY + datetime.timedelta(days=400)

    forecast = await _get(api_client, employee, own, far)

    assert forecast.json()["is_forecast"] is True
    main = _main(forecast)
    other = next(b for b in forecast.json()["balances"] if b["code"] == "time_off")
    recorded_other = next(b for b in recorded.json()["balances"] if b["code"] == "time_off")
    assert main["forecast_accrual"] is not None  # у основного вида прогноз
    assert Decimal(main["total_balance"]) == expected_total_accrual(HIRE, far)
    assert other["forecast_accrual"] is None  # у второго - записанное, не 0
    assert Decimal(other["total_balance"]) == Decimal("5")
    assert other["total_balance"] == recorded_other["total_balance"]
    assert Decimal(other["balance"]) == 0  # год даты (с 27.04.2027) событий не имеет: записанное
    assert await _event_count(db_session, employee.id) == before  # журнал не изменился
