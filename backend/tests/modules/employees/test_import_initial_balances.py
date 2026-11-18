"""Начальные остатки в импорте сотрудников (`T-M1-36`, `FR-EMP-003/3`…`/8`, `FR-BAL-001/4`).

Остаток пишется ТОЛЬКО функцией модуля `balances` (`import_initial_balance`), в одной транзакции с
карточкой; фаза 1 отклоняет строку, а запись фазы 2 отказать уже не может, поэтому отказ строки
проверяется тут же, а отказ всего импорта - отдельным тестом.
"""

from __future__ import annotations

import datetime
import itertools
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.models.employee import Employee
from app.models.leave_balance_entry import INITIAL_IMPORT, LeaveBalanceEntry
from app.modules.balances.service import BalanceEntryError, accrue_before_read, get_balance
from app.modules.business_rules.business_day import business_today
from app.modules.employees import import_service
from app.modules.employees.import_parsing import ParsedRow
from app.modules.employees.import_service import import_employees
from tests.modules.auth.factories import make_account, make_department

_seq = itertools.count(1)
_COLUMNS = {
    "annual_paid": "initial_balance_annual_paid",
    "additional_paid": "initial_balance_additional_paid",
    "time_off": "initial_balance_time_off",
}


def _row(row_number: int, **overrides: str | None) -> ParsedRow:
    n = next(_seq)
    values: dict[str, str | None] = {
        "full_name": f"Employee {n}",
        "personnel_number": f"BAL-{n}",
        "department": "IT",
        "position": "Инженер",
        "hire_date": "2015-03-10",
        "email": f"bal-{n}@company.local",
        "work_schedule_type": "standard",
        "manager_personnel_number": None,
        "leave_category": None,
        "initial_balance_annual_paid": None,
        "initial_balance_additional_paid": None,
        "initial_balance_time_off": None,
    }
    values.update(overrides)
    return ParsedRow(row_number=row_number, values=values)


async def _author(db_session) -> int:
    return (await make_account(db_session, login=f"imp-{next(_seq)}", role="hr_admin")).id


async def _entries(db_session, personnel_number: str) -> list[LeaveBalanceEntry]:
    employee_id = await db_session.scalar(
        select(Employee.id).where(Employee.personnel_number == personnel_number)
    )
    if employee_id is None:
        return []
    return list(
        (
            await db_session.execute(
                select(LeaveBalanceEntry).where(LeaveBalanceEntry.employee_id == employee_id)
            )
        ).scalars()
    )


async def _type_ids(db_session) -> dict[str, int]:
    rows = await db_session.execute(text("SELECT code, id FROM leave_types"))
    return {row.code: row.id for row in rows}


async def test_fr_emp_003_4_three_kinds_give_three_events_one_per_non_empty_column(
    db_session,
) -> None:
    # FR-EMP-003/4.
    await make_department(db_session, name="IT")
    author = await _author(db_session)
    row = _row(
        2,
        personnel_number="TRIPLE",
        initial_balance_annual_paid="12,5",
        initial_balance_additional_paid="3",
        initial_balance_time_off="1.25",
    )

    result = await import_employees(db_session, [row], actor_account_id=author)

    assert (result.accepted_count, result.rejected_count, result.errors) == (1, 0, [])
    entries = await _entries(db_session, "TRIPLE")
    ids = await _type_ids(db_session)
    assert {e.leave_type_id: e.quantity for e in entries} == {
        ids["annual_paid"]: Decimal("12.50"),
        ids["additional_paid"]: Decimal("3.00"),
        ids["time_off"]: Decimal("1.25"),
    }
    today = await business_today(db_session)
    assert {e.entry_type for e in entries} == {INITIAL_IMPORT}
    assert {e.entry_date for e in entries} == {today}  # бизнес-день импорта
    # рабочий год, в который попадает день импорта: приём 10.03.2015 -> год от годовщины
    for entry in entries:
        assert entry.working_year_start <= today < entry.working_year_end
        assert (entry.working_year_start.month, entry.working_year_start.day) == (3, 10)
    employee_id = entries[0].employee_id
    assert await get_balance(
        db_session, employee_id, ids["annual_paid"], entries[0].working_year_start
    ) == Decimal("12.50")


async def test_fr_emp_003_5_zero_and_blank_write_no_event_and_are_not_errors(db_session) -> None:
    # FR-EMP-003/5.
    await make_department(db_session, name="IT")
    author = await _author(db_session)
    rows = [
        _row(2, personnel_number="ZERO", initial_balance_annual_paid="0"),
        _row(3, personnel_number="ZERO2", initial_balance_annual_paid="0,00"),
        _row(4, personnel_number="BLANK"),
    ]

    result = await import_employees(db_session, rows, actor_account_id=author)

    assert (result.accepted_count, result.rejected_count, result.errors) == (3, 0, [])
    for personnel_number in ("ZERO", "ZERO2", "BLANK"):
        assert await _entries(db_session, personnel_number) == []


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ("-1", "отрицательный"),
        ("-0", "отрицательный"),
        ("abc", "некорректный"),
        ("1,234", "некорректный"),
        ("1e2", "некорректный"),
        ("100000", "некорректный"),
        ("1 5", "некорректный"),
    ],
)
async def test_fr_emp_003_6_a_bad_number_rejects_the_row_and_creates_no_card(
    db_session, raw: str, fragment: str
) -> None:
    # FR-EMP-003/6.
    await make_department(db_session, name="IT")
    author = await _author(db_session)
    rows = [
        _row(2, personnel_number="BAD", initial_balance_time_off=raw),
        _row(3, personnel_number="GOOD", initial_balance_annual_paid="5"),
    ]

    result = await import_employees(db_session, rows, actor_account_id=author)

    assert (result.accepted_count, result.rejected_count) == (1, 1)  # остальные загружены
    assert [(e.row_number, e.field) for e in result.errors] == [(2, "initial_balance_time_off")]
    assert fragment in result.errors[0].reason
    bad_id = await db_session.scalar(select(Employee.id).where(Employee.personnel_number == "BAD"))
    assert bad_id is None
    assert await _entries(db_session, "BAD") == []
    assert len(await _entries(db_session, "GOOD")) == 1


async def test_fr_emp_003_6_a_positive_balance_with_a_future_hire_date_is_a_row_error(
    db_session,
) -> None:
    # FR-EMP-003/6.
    await make_department(db_session, name="IT")
    author = await _author(db_session)
    future = (await business_today(db_session) + datetime.timedelta(days=30)).isoformat()
    rows = [
        _row(2, personnel_number="FUT", hire_date=future, initial_balance_annual_paid="5"),
        # ноль у будущего приёма - не остаток, ошибки нет
        _row(3, personnel_number="FUT0", hire_date=future, initial_balance_annual_paid="0"),
    ]

    result = await import_employees(db_session, rows, actor_account_id=author)

    assert (result.accepted_count, result.rejected_count) == (1, 1)
    assert [(e.row_number, e.field) for e in result.errors] == [(2, "initial_balance_annual_paid")]
    assert "позже дня импорта" in result.errors[0].reason


async def test_fr_emp_003_6_a_positive_balance_of_an_untracked_type_is_a_row_error(
    db_session,
) -> None:
    # FR-EMP-003/6.
    await make_department(db_session, name="IT")
    author = await _author(db_session)
    await db_session.execute(
        text("UPDATE leave_types SET balance_tracked = false WHERE code = 'time_off'")
    )

    result = await import_employees(
        db_session,
        [_row(2, personnel_number="UNT", initial_balance_time_off="2")],
        actor_account_id=author,
    )

    assert (result.accepted_count, result.rejected_count) == (0, 1)
    assert result.errors[0].field == "initial_balance_time_off"
    assert "остаток не ведётся" in result.errors[0].reason


async def test_fr_emp_003_7_warnings_are_exactly_old_hires_without_a_main_balance(
    db_session,
) -> None:
    # FR-EMP-003/7.
    await make_department(db_session, name="IT")
    author = await _author(db_session)
    today = (await business_today(db_session)).isoformat()
    rows = [
        _row(2, personnel_number="W-OLD"),  # приём раньше, остатка нет -> предупреждение
        _row(3, personnel_number="W-MAIN", initial_balance_annual_paid="4"),  # есть -> нет
        _row(4, personnel_number="W-TODAY", hire_date=today),  # приём в день импорта -> нет
        # остаток только по отгулам: по основному виду его нет -> предупреждение
        _row(5, personnel_number="W-TOFF", initial_balance_time_off="2"),
        _row(6, personnel_number="W-ZERO", initial_balance_annual_paid="0"),  # ноль = нет остатка
        _row(7, personnel_number="W-REJ", initial_balance_annual_paid="-3"),  # отклонена -> нет
    ]

    result = await import_employees(db_session, rows, actor_account_id=author)

    assert result.warning_rows == [2, 5, 6]
    assert result.rejected_count == 1  # предупреждение отклонённой строкой не считается
    assert [e.row_number for e in result.errors] == [7]


async def test_fr_emp_003_8_the_author_is_the_account_that_ran_the_import(db_session) -> None:
    # FR-EMP-003/8.
    # Скриптом при первом развёртывании автор - первичный администратор (`FR-AUTH-005`): тот же
    # параметр, скрипта в репозитории нет. Здесь - учётная запись администратора как автор.
    await make_department(db_session, name="IT")
    bootstrap_like = await make_account(
        db_session, login="bootstrap-like", auth_type="local", role="system_admin"
    )

    await import_employees(
        db_session,
        [_row(2, personnel_number="AUTH", initial_balance_annual_paid="7")],
        actor_account_id=bootstrap_like.id,
    )

    (entry,) = await _entries(db_session, "AUTH")
    assert entry.actor_account_id == bootstrap_like.id


async def test_fr_emp_003_8_a_balance_without_an_author_aborts_the_whole_import(
    db_session,
) -> None:
    # FR-EMP-003/8.
    await make_department(db_session, name="IT")
    rows = [
        _row(2, personnel_number="NOAUTH-1"),
        _row(3, personnel_number="NOAUTH-2", initial_balance_annual_paid="7"),
    ]

    with pytest.raises(BalanceEntryError, match="автор"):
        await import_employees(db_session, rows)

    # ничего не записано, в том числе карточка строки БЕЗ остатка: отказ записи остатка
    # откатывает весь импорт, а не строку (отклонение строки возможно только в фазе 1)
    for personnel_number in ("NOAUTH-1", "NOAUTH-2"):
        assert (
            await db_session.scalar(
                select(Employee.id).where(Employee.personnel_number == personnel_number)
            )
            is None
        )


async def test_fr_emp_003_4_a_failure_of_the_balance_write_rolls_the_cards_back(
    db_session, monkeypatch
) -> None:
    # FR-EMP-003/4.
    await make_department(db_session, name="IT")
    author = await _author(db_session)

    async def _boom(*args, **kwargs):
        raise BalanceEntryError("сбой записи остатка")

    monkeypatch.setattr(import_service, "import_initial_balance", _boom)

    with pytest.raises(BalanceEntryError, match="сбой"):
        await import_employees(
            db_session,
            [_row(2, personnel_number="ATOMIC", initial_balance_annual_paid="3")],
            actor_account_id=author,
        )

    assert (
        await db_session.scalar(select(Employee.id).where(Employee.personnel_number == "ATOMIC"))
        is None
    )


async def test_fr_bal_001_4_after_the_import_nothing_is_accrued_up_to_the_import_date(
    db_session,
) -> None:
    # Импорт сегодня: все шаги до этого дня покрыты остатком, годы до года импорта закрыты
    # целиком (приём 2015 - карточка заведена сегодня, барьер тоже). Читающее начисление на
    # день импорта ничего не пишет, журнал остаётся из одного события.
    await make_department(db_session, name="IT")
    author = await _author(db_session)
    await import_employees(
        db_session,
        [_row(2, personnel_number="ACC", initial_balance_annual_paid="10")],
        actor_account_id=author,
    )
    employee_id = await db_session.scalar(
        select(Employee.id).where(Employee.personnel_number == "ACC")
    )
    today = await business_today(db_session)

    result = await accrue_before_read(db_session, employee_id=employee_id, as_of=today)

    assert result.accrued == {}
    assert len(await _entries(db_session, "ACC")) == 1
