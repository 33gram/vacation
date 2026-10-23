"""Чистая арифметика накопления отпуска (T-M2-05, `FR-BAL-001/1`,
`FR-BAL-001/2`): без сессии и без БД.

Ожидаемые числа посчитаны в тесте ЯВНО, на бумаге, независимо от
реализации. `FR-BAL-002`/`FR-BAL-004` покрыты в `test_service.py` (`T-M2-08`)
и здесь не повторяются.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from app.modules.balances.accrual import (
    completed_months,
    compute_accrual,
    excluded_unpaid_leave_days,
)

HIRE = datetime.date(2026, 3, 10)
NORM = 28
THRESHOLD = 15
D = datetime.date


def _accrue(
    as_of: datetime.date,
    *,
    excluded: int = 0,
    threshold: int = THRESHOLD,
    year_start: datetime.date | None = None,
):
    return compute_accrual(
        hire_date=HIRE,
        as_of=as_of,
        annual_norm=NORM,
        rounding_threshold_days=threshold,
        excluded_days=excluded,
        working_year_start=year_start,
    )


def test_fr_bal_001_full_working_year_accrues_exactly_the_norm() -> None:
    # Последний день первого рабочего года - 09.03.2027 включительно:
    # 12 полных месяцев, 28 * 12 / 12 = 28 ровно (месячная константа
    # 2,33 дала бы 27,96).
    result = _accrue(D(2027, 3, 9))

    assert result.full_months == 12
    assert result.accrued == Decimal("28.00")


def test_fr_bal_001_mid_year_hire_accrues_proportionally_with_the_half_month_rounded_up() -> None:
    # Приём 10.03.2026, расчёт 24.10.2026: 7 полных месяцев (седьмой
    # кончается 09.10), остаток 10.10-24.10 = 15 дней >= 15 -> 8 месяцев;
    # 28 * 8 / 12 = 18,6667 -> 18,67.
    result = _accrue(D(2026, 10, 24))

    assert (result.full_months, result.remainder_days) == (7, 15)
    assert result.counted_months == 8
    assert result.accrued == Decimal("18.67")


def test_fr_bal_001_remainder_one_day_below_the_threshold_adds_no_month() -> None:
    # 23.10: остаток 10.10-23.10 = 14 дней < 15 -> остаются 7 месяцев;
    # 28 * 7 / 12 = 16,3333 -> 16,33.
    result = _accrue(D(2026, 10, 23))

    assert (result.full_months, result.remainder_days) == (7, 14)
    assert result.counted_months == 7
    assert result.accrued == Decimal("16.33")


def test_fr_bal_001_the_threshold_is_a_parameter_not_a_constant() -> None:
    # Тот же остаток 14 дней при пороге 14 даёт месяц - порог читается из
    # параметра (rule 5), а не зашит.
    assert _accrue(D(2026, 10, 23), threshold=14).counted_months == 8


def test_fr_bal_001_working_year_is_counted_from_the_hire_date_anniversaries() -> None:
    result = _accrue(D(2027, 3, 10))  # первый день второго рабочего года

    assert result.working_year_start == D(2027, 3, 10)
    assert result.working_year_end == D(2028, 3, 10)
    assert result.counted_months == 0
    assert result.accrued == Decimal("0.00")


def test_fr_bal_001_first_working_year_boundaries_without_exclusions() -> None:
    result = _accrue(D(2026, 10, 24))

    assert result.working_year_start == D(2026, 3, 10)
    assert result.working_year_end == D(2027, 3, 10)  # конец исключительно


def test_fr_bal_001_the_total_is_rounded_once_at_the_end_half_up() -> None:
    # 1 месяц от нормы 30: 30 / 12 = 2,5 ровно; 30 * 1 / 12 с округлением
    # итога half-up -> 2,50; норма 29: 29 / 12 = 2,41666 -> 2,42.
    one_month = compute_accrual(
        hire_date=HIRE,
        as_of=D(2026, 4, 9),
        annual_norm=29,
        rounding_threshold_days=THRESHOLD,
        excluded_days=0,
    )

    assert one_month.full_months == 1
    assert one_month.accrued == Decimal("2.42")


def test_fr_bal_001_exact_half_cent_rounds_up_not_to_even() -> None:
    # 1,5 дня нормы, 1 месяц: 1,5 / 12 = 0,125 -> 0,13 (half-up), а не 0,12
    # (банковское округление).
    result = compute_accrual(
        hire_date=HIRE,
        as_of=D(2026, 4, 9),
        annual_norm=Decimal("1.5"),
        rounding_threshold_days=THRESHOLD,
        excluded_days=0,
    )

    assert result.accrued == Decimal("0.13")


def test_fr_bal_001_excluded_days_shift_the_year_end_and_the_month_boundaries() -> None:
    # 16 исключённых дней: конец рабочего года 10.03.2027 + 16 дней =
    # 26.03.2027 (исключительно); накопление на 09.03.2027 - не 12 месяцев:
    # 16 дней из прошедшего времени не считаются.
    result = _accrue(D(2027, 3, 9), excluded=16)

    assert result.working_year_end == D(2027, 3, 26)
    assert result.full_months == 11
    assert result.accrued < Decimal("28.00")


def test_fr_bal_001_with_the_shifted_end_the_year_reaches_the_norm_on_its_new_last_day() -> None:
    # Дата в «хвосте» сдвинутого года: по годовщинам это уже второй год,
    # поэтому вызывающий называет начало прежнего.
    result = _accrue(D(2027, 3, 25), excluded=16, year_start=HIRE)

    assert result.working_year_end == D(2027, 3, 26)
    assert result.counted_months == 12
    assert result.accrued == Decimal("28.00")


def test_fr_bal_001_three_unpaid_leaves_of_ten_days_exclude_sixteen_days() -> None:
    # Три отпуска по 10 дней в одном рабочем году: сумма 30, засчитывается 14,
    # исключается 16. Формулировка "свыше 14 дней ПОДРЯД" не исключила бы
    # ничего (каждый период 10 < 14).
    assert excluded_unpaid_leave_days([10, 10, 10], counted_limit=14) == 16


def test_fr_bal_001_unpaid_leave_totalling_exactly_the_limit_excludes_nothing() -> None:
    assert excluded_unpaid_leave_days([7, 7], counted_limit=14) == 0
    assert excluded_unpaid_leave_days([14], counted_limit=14) == 0


def test_fr_bal_001_one_day_over_the_limit_excludes_one_day() -> None:
    assert excluded_unpaid_leave_days([8, 7], counted_limit=14) == 1


def test_fr_bal_001_no_unpaid_leave_excludes_nothing() -> None:
    assert excluded_unpaid_leave_days([], counted_limit=14) == 0


def test_fr_bal_001_the_counted_limit_is_a_parameter() -> None:
    assert excluded_unpaid_leave_days([10, 10, 10], counted_limit=10) == 20


def test_fr_bal_001_a_date_in_the_shifted_tail_without_a_year_start_is_the_next_year() -> None:
    # Граница, названная в докстринге accrual.py: без `working_year_start`
    # год определяется годовщинами.
    result = _accrue(D(2027, 3, 25), excluded=0)

    assert result.working_year_start == D(2027, 3, 10)


def test_fr_bal_001_a_year_start_that_is_not_an_anniversary_is_refused() -> None:
    with pytest.raises(ValueError):
        _accrue(D(2026, 10, 24), year_start=D(2026, 4, 1))


def test_fr_bal_001_a_date_outside_the_named_working_year_is_refused() -> None:
    with pytest.raises(ValueError):
        _accrue(D(2027, 3, 27), excluded=16, year_start=HIRE)  # конец - 26.03, исключительно


def test_fr_bal_001_the_calculation_date_before_the_hire_date_is_refused() -> None:
    with pytest.raises(ValueError):
        _accrue(D(2026, 3, 9))


def test_fr_bal_001_negative_excluded_days_are_refused() -> None:
    with pytest.raises(ValueError):
        _accrue(D(2026, 10, 24), excluded=-1)


def test_fr_bal_001_hire_on_the_31st_clamps_month_boundaries_to_the_month_end() -> None:
    # Приём 31.01.2026: границы месяцев - 28.02, 31.03: на 27.02 полных
    # месяцев ещё 0, на 28.02 - уже 1 (месяц кончился 27.02 включительно).
    def accrue(as_of: datetime.date):
        return compute_accrual(
            hire_date=D(2026, 1, 31),
            as_of=as_of,
            annual_norm=NORM,
            rounding_threshold_days=31,
            excluded_days=0,
        )

    assert accrue(D(2026, 2, 26)).full_months == 0
    assert accrue(D(2026, 2, 27)).full_months == 1


# --- Границы месяцев и годовщины на датах, где верное и мутированное поведение
# расходятся (PR #179 review, п. 1б). Каждый тест проверен красным прогоном на
# своей мутации (тело коммита).


def test_fr_bal_001_february_of_a_leap_year_ends_the_first_month_on_the_29th() -> None:
    # Приём 31.01.2024: граница первого месяца - 29.02.2024. На 27.02 отработано
    # до 28.02 включительно - полных месяцев 0 (при зажатии февраля до 28 было бы 1).
    hire = D(2024, 1, 31)

    assert completed_months(hire, D(2024, 2, 27)) == 0
    assert completed_months(hire, D(2024, 2, 28)) == 1  # 29.02 - граница, месяц полон


def test_fr_bal_001_month_boundaries_are_counted_from_the_hire_date_not_chained() -> None:
    # Приём 31.01.2026: границы 28.02 и 31.03. На 28.03 полный месяц один (у
    # цепочки 28.02 + 1 месяц = 28.03 было бы два).
    hire = D(2026, 1, 31)

    assert completed_months(hire, D(2026, 3, 28)) == 1
    assert completed_months(hire, D(2026, 3, 30)) == 2  # 31.03 - граница второго месяца


def test_fr_bal_001_the_anniversary_of_a_29_february_hire_is_the_28th_not_1_march() -> None:
    # Приём 29.02.2024: годовщина 2025 года - 28.02.2025. На 28.02.2025
    # начинается второй рабочий год: накоплено 0,00 (при годовщине 01.03 год
    # остался бы первым и накопление вышло бы около 28,00).
    result = compute_accrual(
        hire_date=D(2024, 2, 29),
        as_of=D(2025, 2, 28),
        annual_norm=NORM,
        rounding_threshold_days=THRESHOLD,
        excluded_days=0,
    )

    assert result.working_year_start == D(2025, 2, 28)
    assert result.accrued == Decimal("0.00")


def test_fr_bal_001_accrual_and_tenure_use_the_same_month_boundaries() -> None:
    # Единый примитив: полные месяцы накопления внутри первого года равны
    # стажу на тот же день (приём 31.01.2026, каждый день первых четырёх месяцев).
    hire = D(2026, 1, 31)
    day = hire
    while day < D(2026, 5, 31):
        accrual = compute_accrual(
            hire_date=hire,
            as_of=day,
            annual_norm=NORM,
            rounding_threshold_days=THRESHOLD,
            excluded_days=0,
        )
        assert accrual.full_months == completed_months(hire, day), day
        day += datetime.timedelta(days=1)


def test_fr_bal_001_a_rounding_threshold_below_one_day_is_refused() -> None:
    with pytest.raises(ValueError):
        _accrue(D(2026, 10, 24), threshold=0)
