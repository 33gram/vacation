"""Сервис чтения и проверки параметров бизнес-правил (T-M2-09).

Каждый «громкий отказ» проверен тем, что в сообщении есть КЛЮЧ, а не
только тем, что исключение поднялось. Негодные значения, которые CHECK
базы не пропускает (не цифры, `13-01`), проверяются на чистой функции
`parse_stored_value`; значения, допустимые для базы, но недопустимые для
реестра (`0`, `1000`, `02-30`), — вставкой в таблицу.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.modules.auth.password_policy import (
    DEFAULT_LOCKOUT_DURATION,
    DEFAULT_MAX_FAILED_ATTEMPTS,
    DEFAULT_MIN_PASSWORD_LENGTH,
)
from app.modules.business_rules import service
from app.modules.business_rules.service import (
    RULES,
    BusinessRuleError,
    BusinessRulesConfigError,
    MonthDay,
    RuleSpec,
    get_int,
    get_month_day,
    get_rules,
    parse_stored_value,
    validate_all,
)

# Ожидаемые разобранные значения сидированных параметров — независимо от
# реестра и миграции (сверка таблицы сырых значений — test_seed.py).
_EXPECTED_PARSED = {
    "annual_leave_days": 28,
    "first_leave_eligibility_months": 6,
    "schedule_approval_deadline": MonthDay(12, 17),
    "vacation_start_notice_days": 14,
    "min_leave_part_days": 14,
    "leave_usage_deadline_months": 12,
    "compensation_threshold_days": 28,
    "approval_escalation_working_days": 3,
    "password_min_length": 8,
    "max_failed_login_attempts": 5,
    "lockout_duration_minutes": 15,
}


async def _set(db_session, key: str, value: str, value_type: str | None = None) -> None:
    await db_session.execute(
        text(
            "UPDATE business_rules_config SET value = :value, "
            "value_type = COALESCE(:value_type, value_type) WHERE key = :key"
        ),
        {"key": key, "value": value, "value_type": value_type},
    )


def test_registry_covers_exactly_the_seeded_keys() -> None:
    assert set(service.RULES) == set(_EXPECTED_PARSED)


async def test_every_seeded_parameter_is_read_with_the_expected_type_and_value(db_session) -> None:
    values = await get_rules(db_session, *_EXPECTED_PARSED)

    assert values == _EXPECTED_PARSED


async def test_typed_accessors_return_the_typed_value(db_session) -> None:
    assert await get_int(db_session, "annual_leave_days") == 28
    assert await get_month_day(db_session, "schedule_approval_deadline") == MonthDay(12, 17)


def test_month_day_resolves_to_a_date_in_a_given_year() -> None:
    import datetime

    assert MonthDay(12, 17).on(2026) == datetime.date(2026, 12, 17)


async def test_password_parameters_equal_the_defaults_that_were_in_the_code(db_session) -> None:
    # Поведение парольной политики при сидированных значениях не меняется:
    # 8 символов, 5 попыток, 15 минут (T-M2-09, перенос без смены поведения).
    values = await get_rules(
        db_session, "password_min_length", "max_failed_login_attempts", "lockout_duration_minutes"
    )

    assert values["password_min_length"] == DEFAULT_MIN_PASSWORD_LENGTH
    assert values["max_failed_login_attempts"] == DEFAULT_MAX_FAILED_ATTEMPTS
    assert values["lockout_duration_minutes"] * 60 == DEFAULT_LOCKOUT_DURATION.total_seconds()


async def test_an_edit_is_visible_to_the_next_read_there_is_no_cache(db_session) -> None:
    assert await get_int(db_session, "annual_leave_days") == 28

    await _set(db_session, "annual_leave_days", "30")

    assert await get_int(db_session, "annual_leave_days") == 30


# FR-AUTH-002/15: настраиваемая минимальная длина пароля — от 8 до 72
# (строки `password_min_length` 7 и 73 ниже); остальные строки —
# диапазоны реестра, выбранные реализацией.
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("annual_leave_days", "0"),
        ("annual_leave_days", "1000"),
        ("password_min_length", "7"),
        ("password_min_length", "73"),
        ("max_failed_login_attempts", "0"),
        ("lockout_duration_minutes", "0"),
        ("approval_escalation_working_days", "0"),
    ],
)
async def test_integer_outside_the_range_is_refused_naming_the_key(
    db_session, key: str, value: str
) -> None:
    await _set(db_session, key, value)

    with pytest.raises(BusinessRuleError) as excinfo:
        await get_int(db_session, key)

    assert excinfo.value.key == key
    assert key in str(excinfo.value)
    assert "range" in str(excinfo.value)


@pytest.mark.parametrize("value", ["8", "72"])
async def test_password_min_length_accepts_both_bounds_of_fr_auth_002_15(
    db_session, value: str
) -> None:
    await _set(db_session, "password_min_length", value)

    assert await get_int(db_session, "password_min_length") == int(value)


@pytest.mark.parametrize("value", ["02-30", "02-29", "04-31"])
async def test_a_date_that_does_not_exist_is_refused_naming_the_key(db_session, value: str) -> None:
    await _set(db_session, "schedule_approval_deadline", value)

    with pytest.raises(BusinessRuleError) as excinfo:
        await get_month_day(db_session, "schedule_approval_deadline")

    assert excinfo.value.key == "schedule_approval_deadline"


async def test_a_stored_type_that_differs_from_the_registry_is_refused(db_session) -> None:
    await _set(db_session, "annual_leave_days", "12-17", "month_day")

    with pytest.raises(BusinessRuleError) as excinfo:
        await get_int(db_session, "annual_leave_days")

    assert "annual_leave_days" in str(excinfo.value)
    assert "value_type" in str(excinfo.value)


# Проверки типа - настоящие исключения, а не `assert`: под `python -O`
# `assert` исчезает (PR #176 review, С-7).
async def test_get_int_on_a_month_day_rule_is_refused_naming_the_key(db_session) -> None:
    with pytest.raises(BusinessRuleError) as excinfo:
        await get_int(db_session, "schedule_approval_deadline")

    assert excinfo.value.key == "schedule_approval_deadline"
    assert "integer" in str(excinfo.value)


async def test_get_month_day_on_an_integer_rule_is_refused_naming_the_key(db_session) -> None:
    with pytest.raises(BusinessRuleError) as excinfo:
        await get_month_day(db_session, "annual_leave_days")

    assert excinfo.value.key == "annual_leave_days"
    assert "month_day" in str(excinfo.value)


def test_an_integer_rule_without_a_range_in_the_registry_is_refused(monkeypatch) -> None:
    monkeypatch.setitem(RULES, "annual_leave_days", RuleSpec("annual_leave_days", "integer"))

    with pytest.raises(BusinessRuleError) as excinfo:
        parse_stored_value("annual_leave_days", "28", "integer")

    assert excinfo.value.key == "annual_leave_days"
    assert "range" in str(excinfo.value)


async def test_a_missing_key_is_refused_naming_the_key_and_not_defaulted(db_session) -> None:
    await db_session.execute(
        text("DELETE FROM business_rules_config WHERE key = 'min_leave_part_days'")
    )

    with pytest.raises(BusinessRuleError) as excinfo:
        await get_int(db_session, "min_leave_part_days")

    assert excinfo.value.key == "min_leave_part_days"
    assert "missing" in str(excinfo.value)


@pytest.mark.parametrize("value", ["", "-1", "1.5", "abc", "１２"])
def test_non_integer_text_is_refused_naming_the_key(value: str) -> None:
    with pytest.raises(BusinessRuleError) as excinfo:
        parse_stored_value("annual_leave_days", value, "integer")

    assert excinfo.value.key == "annual_leave_days"


# Вторая половина списка — то, что `CHECK` базы отвергает, а `int()` и
# `date()` без проверки формата принимают (измерено при ревью PR #176, Сп-1):
# сервис не должен быть мягче базы, иначе запись `T-M2-12`, проверенная
# сервисом, упадёт `IntegrityError` (500 вместо 4xx).
@pytest.mark.parametrize(
    "value",
    ["", "13-01", "1217", "12-3x", "ab-cd", "1-5", " 12-17", "+1-+5", "012-017", "١٢-١٧"],
)
def test_malformed_month_day_text_is_refused_naming_the_key(value: str) -> None:
    with pytest.raises(BusinessRuleError) as excinfo:
        parse_stored_value("schedule_approval_deadline", value, "month_day")

    assert excinfo.value.key == "schedule_approval_deadline"


def test_a_key_outside_the_registry_is_refused() -> None:
    with pytest.raises(BusinessRuleError) as excinfo:
        parse_stored_value("made_up_rule", "1", "integer")

    assert excinfo.value.key == "made_up_rule"


async def test_startup_validation_passes_on_the_seeded_table(db_session) -> None:
    await validate_all(db_session)


async def test_startup_validation_lists_every_problem_not_the_first(db_session) -> None:
    await db_session.execute(
        text("DELETE FROM business_rules_config WHERE key = 'min_leave_part_days'")
    )
    await _set(db_session, "annual_leave_days", "0")
    await _set(db_session, "schedule_approval_deadline", "02-30")

    with pytest.raises(BusinessRulesConfigError) as excinfo:
        await validate_all(db_session)

    assert {problem.key for problem in excinfo.value.problems} == {
        "min_leave_part_days",
        "annual_leave_days",
        "schedule_approval_deadline",
    }
    message = str(excinfo.value)
    assert all(key in message for key in ("min_leave_part_days", "annual_leave_days"))
