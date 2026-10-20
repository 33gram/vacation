"""Ограничения `leave_types` на уровне БД (T-M2-01).

Вставка идёт СЫРЫМ SQL, в обход ORM и pydantic: проверяется CHECK базы,
а не валидация приложения. Контрольная вставка с допустимыми значениями
доказывает, что отказ вызван именно проверяемым значением, а не чем-то
другим в строке.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

_VALID = {
    "code": "constraint_probe",
    "name": "Проба",
    "is_paid": "full",
    "unit": "calendar_days",
    "reduces_main_balance": False,
    "requires_document": False,
    "requires_approval": True,
    "annual_limit": None,
    "carry_over_policy": "none",
    "counted_non_working_days": "all",
    "is_active": True,
}

_INSERT = text(
    "INSERT INTO leave_types (code, name, is_paid, unit, reduces_main_balance, "
    "requires_document, requires_approval, annual_limit, carry_over_policy, "
    "counted_non_working_days, is_active) VALUES (:code, :name, :is_paid, :unit, "
    ":reduces_main_balance, :requires_document, :requires_approval, :annual_limit, "
    ":carry_over_policy, :counted_non_working_days, :is_active)"
)


async def test_valid_row_is_accepted(db_session) -> None:
    await db_session.execute(_INSERT, _VALID)


@pytest.mark.parametrize(
    ("column", "bad_value", "constraint"),
    [
        ("is_paid", "mostly", "ck_leave_types_is_paid"),
        ("unit", "weeks", "ck_leave_types_unit"),
        ("carry_over_policy", "up_to_n_days", "ck_leave_types_carry_over_policy"),
        ("counted_non_working_days", "holidays_only", "ck_leave_types_counted_non_working_days"),
        ("annual_limit", -1, "ck_leave_types_annual_limit_non_negative"),
    ],
)
async def test_value_outside_the_set_is_rejected_by_the_database(
    db_session, column: str, bad_value: object, constraint: str
) -> None:
    with pytest.raises(IntegrityError) as excinfo:
        await db_session.execute(_INSERT, {**_VALID, column: bad_value})

    assert constraint in str(excinfo.value)


@pytest.mark.parametrize("value", ["all", "weekends_only", "none"])
async def test_every_counted_non_working_days_value_is_accepted(db_session, value: str) -> None:
    await db_session.execute(_INSERT, {**_VALID, "counted_non_working_days": value})


@pytest.mark.parametrize("value", ["calendar_days", "working_days", "hours"])
async def test_every_unit_value_is_accepted(db_session, value: str) -> None:
    await db_session.execute(_INSERT, {**_VALID, "unit": value})


async def test_duplicate_code_is_rejected(db_session) -> None:
    await db_session.execute(_INSERT, _VALID)

    with pytest.raises(IntegrityError):
        await db_session.execute(_INSERT, _VALID)


@pytest.mark.parametrize(
    "column",
    [
        "code",
        "name",
        "is_paid",
        "unit",
        "reduces_main_balance",
        "requires_document",
        "requires_approval",
        "carry_over_policy",
        "counted_non_working_days",
        "is_active",
    ],
)
async def test_required_column_rejects_null(db_session, column: str) -> None:
    with pytest.raises(IntegrityError):
        await db_session.execute(_INSERT, {**_VALID, column: None})
