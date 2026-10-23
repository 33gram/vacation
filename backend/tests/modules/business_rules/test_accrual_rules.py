"""Параметры расчёта накопления, добавленные миграцией 0009 (T-M2-05,
`FR-BAL-001/1`, `FR-BAL-001/2`).

Таблица и её общее сидирование проверяют `test_seed.py` (там же ожидаемая
таблица всех ключей); здесь — то, что относится именно к 0009: идемпотентность
ЕЁ оператора сидирования (двойной вызов, а не повторный `alembic upgrade`:
ревизия применяется один раз и повтор ничего не проверил бы), диапазоны
реестра и подхват ключей стартовой проверкой.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text

from app.modules.business_rules.service import (
    MONTH_ROUNDING_THRESHOLD_DAYS,
    UNPAID_LEAVE_DAYS_COUNTED_IN_SERVICE,
    BusinessRuleError,
    BusinessRulesConfigError,
    get_int,
    validate_all,
)

_MIGRATION = (
    Path(__file__).resolve().parents[3] / "alembic" / "versions" / "0009_accrual_business_rules.py"
)
_KEYS = (MONTH_ROUNDING_THRESHOLD_DAYS, UNPAID_LEAVE_DAYS_COUNTED_IN_SERVICE)


def _seed_function():
    spec = importlib.util.spec_from_file_location("migration_0009", _MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.seed_accrual_rules


async def _run_seed(db_session) -> None:
    seed = _seed_function()
    await db_session.run_sync(lambda session: seed(session.connection()))


async def _count(db_session, key: str) -> int:
    result = await db_session.execute(
        text("SELECT count(*) FROM business_rules_config WHERE key = :key"), {"key": key}
    )
    return result.scalar_one()


async def test_the_seeded_values_are_the_ones_the_requirements_name(db_session) -> None:
    assert await get_int(db_session, MONTH_ROUNDING_THRESHOLD_DAYS) == 15
    assert await get_int(db_session, UNPAID_LEAVE_DAYS_COUNTED_IN_SERVICE) == 14


async def test_calling_the_seed_operator_twice_creates_no_duplicates(db_session) -> None:
    await _run_seed(db_session)
    await _run_seed(db_session)

    assert [await _count(db_session, key) for key in _KEYS] == [1, 1]


async def test_the_seed_operator_does_not_overwrite_an_admin_edit(db_session) -> None:
    await db_session.execute(
        text("UPDATE business_rules_config SET value = '20' WHERE key = :key"),
        {"key": MONTH_ROUNDING_THRESHOLD_DAYS},
    )

    await _run_seed(db_session)

    assert await get_int(db_session, MONTH_ROUNDING_THRESHOLD_DAYS) == 20


async def test_the_seed_operator_restores_a_missing_key(db_session) -> None:
    await db_session.execute(
        text("DELETE FROM business_rules_config WHERE key = :key"),
        {"key": UNPAID_LEAVE_DAYS_COUNTED_IN_SERVICE},
    )

    await _run_seed(db_session)

    assert await get_int(db_session, UNPAID_LEAVE_DAYS_COUNTED_IN_SERVICE) == 14


@pytest.mark.parametrize(
    ("key", "value"),
    [
        (MONTH_ROUNDING_THRESHOLD_DAYS, "0"),
        (MONTH_ROUNDING_THRESHOLD_DAYS, "32"),
        (UNPAID_LEAVE_DAYS_COUNTED_IN_SERVICE, "367"),
    ],
)
async def test_a_value_outside_the_registry_range_is_refused_naming_the_key(
    db_session, key: str, value: str
) -> None:
    await db_session.execute(
        text("UPDATE business_rules_config SET value = :value WHERE key = :key"),
        {"key": key, "value": value},
    )

    with pytest.raises(BusinessRuleError) as excinfo:
        await get_int(db_session, key)

    assert excinfo.value.key == key


@pytest.mark.parametrize(
    ("key", "value"),
    [
        (MONTH_ROUNDING_THRESHOLD_DAYS, "1"),
        (MONTH_ROUNDING_THRESHOLD_DAYS, "31"),
        (UNPAID_LEAVE_DAYS_COUNTED_IN_SERVICE, "0"),
        (UNPAID_LEAVE_DAYS_COUNTED_IN_SERVICE, "366"),
    ],
)
async def test_both_bounds_of_the_registry_range_are_accepted(
    db_session, key: str, value: str
) -> None:
    await db_session.execute(
        text("UPDATE business_rules_config SET value = :value WHERE key = :key"),
        {"key": key, "value": value},
    )

    assert await get_int(db_session, key) == int(value)


async def test_startup_validation_picks_up_a_missing_accrual_key_without_extra_code(
    db_session,
) -> None:
    await db_session.execute(
        text("DELETE FROM business_rules_config WHERE key = :key"),
        {"key": MONTH_ROUNDING_THRESHOLD_DAYS},
    )

    with pytest.raises(BusinessRulesConfigError) as excinfo:
        await validate_all(db_session)

    assert [problem.key for problem in excinfo.value.problems] == [MONTH_ROUNDING_THRESHOLD_DAYS]
