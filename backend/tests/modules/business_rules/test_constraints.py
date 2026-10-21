"""Ограничения `business_rules_config` на уровне БД (T-M2-03).

Вставка идёт СЫРЫМ SQL, в обход ORM: проверяется CHECK/UNIQUE/FK базы, а не
валидация приложения. Контрольная вставка с допустимыми значениями
доказывает, что отказ вызван именно проверяемым значением.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

_VALID = {
    "key": "constraint_probe",
    "value": "7",
    "value_type": "integer",
    "description": "Проба",
    "updated_by_account_id": None,
}

_INSERT = text(
    "INSERT INTO business_rules_config (key, value, value_type, description, "
    "updated_by_account_id) VALUES (:key, :value, :value_type, :description, "
    ":updated_by_account_id)"
)


async def test_valid_row_is_accepted(db_session) -> None:
    await db_session.execute(_INSERT, _VALID)


async def test_valid_month_day_row_is_accepted(db_session) -> None:
    await db_session.execute(_INSERT, {**_VALID, "value": "02-29", "value_type": "month_day"})


@pytest.mark.parametrize(
    ("column", "bad_value", "constraint"),
    [
        ("value_type", "duration", "ck_business_rules_config_value_type"),
        ("value", "-1", "ck_business_rules_config_integer_format"),
        ("value", "1.5", "ck_business_rules_config_integer_format"),
        ("value", "", "ck_business_rules_config_integer_format"),
        ("value", "28 дней", "ck_business_rules_config_integer_format"),
        ("key", "Upper", "ck_business_rules_config_key_format"),
        ("key", "1starts_with_digit", "ck_business_rules_config_key_format"),
        ("key", "has space", "ck_business_rules_config_key_format"),
    ],
)
async def test_value_outside_the_set_is_rejected_by_the_database(
    db_session, column: str, bad_value: object, constraint: str
) -> None:
    with pytest.raises(IntegrityError) as excinfo:
        await db_session.execute(_INSERT, {**_VALID, column: bad_value})

    assert constraint in str(excinfo.value)


@pytest.mark.parametrize(
    "bad_value", ["13-01", "00-10", "12-32", "12-00", "1-1", "12/17", "12-17 "]
)
async def test_malformed_month_day_is_rejected_by_the_database(db_session, bad_value: str) -> None:
    with pytest.raises(IntegrityError) as excinfo:
        await db_session.execute(_INSERT, {**_VALID, "value": bad_value, "value_type": "month_day"})

    assert "ck_business_rules_config_month_day_format" in str(excinfo.value)


async def test_duplicate_key_is_rejected(db_session) -> None:
    await db_session.execute(_INSERT, _VALID)

    with pytest.raises(IntegrityError) as excinfo:
        await db_session.execute(_INSERT, _VALID)

    assert "business_rules_config_key_key" in str(excinfo.value)


@pytest.mark.parametrize("column", ["key", "value", "value_type", "description"])
async def test_required_column_rejects_null(db_session, column: str) -> None:
    with pytest.raises(IntegrityError) as excinfo:
        await db_session.execute(_INSERT, {**_VALID, column: None})

    assert "not-null" in str(excinfo.value).lower() or "null value" in str(excinfo.value)


async def test_updated_by_must_reference_an_existing_account(db_session) -> None:
    with pytest.raises(IntegrityError) as excinfo:
        await db_session.execute(_INSERT, {**_VALID, "updated_by_account_id": 2_000_000_000})

    assert "foreign key" in str(excinfo.value).lower()
