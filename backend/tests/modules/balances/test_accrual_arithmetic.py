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
    MONTHS_IN_YEAR,
    accrual_steps,
    completed_months,
    compute_accrual,
    excluded_unpaid_leave_days,
    working_year_start_on,
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


# --- Шаги накопления (T-M2-21) -------------------------------------------------
# Даты шагов ИЗМЕРЕНЫ перебором дней по `compute_accrual` до написания функции
# (сводка - в отметке T-M2-21): `accrued` растёт ровно один раз в месяц, в первый
# день, когда остаток неполного месяца достиг порога, а если порог длиннее месяца -
# в последний день месяца (день, когда месяц стал полным).


def _steps(hire: datetime.date, year_index: int, *, norm=NORM, threshold=THRESHOLD, excluded=0):
    start = working_year_start_on(hire, year_index)
    return accrual_steps(
        hire_date=hire,
        working_year_start=start,
        as_of=start.replace(year=start.year + 2),  # заведомо позже конца года
        annual_norm=norm,
        rounding_threshold_days=threshold,
        excluded_days=excluded,
    )


def test_steps_of_an_ordinary_year_are_dated_by_the_threshold_day_of_each_month() -> None:
    steps = _steps(HIRE, 0)

    assert [step.date for step in steps] == [
        D(2026, 3, 24), D(2026, 4, 24), D(2026, 5, 24), D(2026, 6, 24),
        D(2026, 7, 24), D(2026, 8, 24), D(2026, 9, 24), D(2026, 10, 24),
        D(2026, 11, 24), D(2026, 12, 24), D(2027, 1, 24), D(2027, 2, 24),
    ]  # fmt: skip
    assert [step.accrued for step in steps][:3] == [
        Decimal("2.33"),
        Decimal("4.67"),
        Decimal("7.00"),
    ]
    assert steps[-1].accrued == Decimal("28.00")


def test_a_threshold_of_one_dates_the_steps_at_the_month_start() -> None:
    steps = _steps(HIRE, 0, threshold=1)

    assert steps[0].date == HIRE and steps[-1].date == D(2027, 2, 10)


def test_a_threshold_longer_than_the_month_dates_the_steps_at_the_last_day_of_the_month() -> None:
    # Порог 31 при месяцах в 30-31 день не достигается: месяц считается, когда он
    # полный, то есть в день перед границей (10.04 - граница, 09.04 - последний день).
    steps = _steps(HIRE, 0, threshold=31)

    assert steps[0].date == D(2026, 4, 9) and steps[-1].date == D(2027, 3, 9)


def test_excluded_days_shift_every_step_by_the_same_number_of_days() -> None:
    plain = _steps(HIRE, 0)
    shifted = _steps(HIRE, 0, excluded=5)

    assert [b.date - a.date for a, b in zip(plain, shifted, strict=True)] == [
        datetime.timedelta(days=5)
    ] * MONTHS_IN_YEAR


def test_the_steps_stop_at_as_of_and_come_out_in_order() -> None:
    start = HIRE
    steps = accrual_steps(
        hire_date=HIRE, working_year_start=start, as_of=D(2026, 6, 24), annual_norm=NORM,
        rounding_threshold_days=THRESHOLD, excluded_days=0,
    )  # fmt: skip

    assert [step.date for step in steps][-1] == D(2026, 6, 24) and len(steps) == 4
    assert accrual_steps(
        hire_date=HIRE, working_year_start=start, as_of=D(2026, 3, 23), annual_norm=NORM,
        rounding_threshold_days=THRESHOLD, excluded_days=0,
    ) == []  # fmt: skip


@pytest.mark.parametrize("norm", [28, 29, 30, 1, 0, Decimal("28.5")])
def test_step_amounts_are_differences_of_rounded_totals_and_sum_to_the_norm(norm) -> None:
    # Двенадцать округлённых по 2,33 дали бы 27,96; разность округлённых накопленных
    # складывается ровно в норму.
    steps = _steps(HIRE, 0, norm=norm)
    totals = [Decimal(0)] + [step.accrued for step in steps]

    amounts = [b - a for a, b in zip(totals, totals[1:], strict=False)]
    assert sum(amounts) == Decimal(norm).quantize(Decimal("0.01"))
    assert len(steps) <= MONTHS_IN_YEAR


@pytest.mark.parametrize(
    ("hire", "year_index"),
    [
        (D(2026, 3, 10), 0),  # обычный
        (D(2026, 1, 31), 0),  # приём 31-го: граница февраля - последний день месяца
        (D(2023, 1, 31), 1),  # год 31.01.2024-30.01.2025, високосный февраль
        (D(2024, 2, 29), 0),  # приём 29 февраля
        (D(2024, 2, 29), 1),  # следующий год без 29 февраля
        (D(2023, 3, 10), 1),  # год содержит 29.02.2024
    ],
)
@pytest.mark.parametrize("threshold", [1, 15, 31])
@pytest.mark.parametrize("excluded", [0, 5])
@pytest.mark.parametrize("norm", [28, 29])
def test_steps_agree_with_compute_accrual_on_every_day_of_the_year(
    hire, year_index, threshold, excluded, norm
) -> None:
    # Критерий T-M2-21: накопленное по шагам до даты == compute_accrual(as_of=дата)
    # для КАЖДОГО дня рабочего года, а не для выбранных дат.
    start = working_year_start_on(hire, year_index)
    end = working_year_start_on(hire, year_index + 1) + datetime.timedelta(days=excluded)
    steps = accrual_steps(
        hire_date=hire, working_year_start=start, as_of=end, annual_norm=norm,
        rounding_threshold_days=threshold, excluded_days=excluded,
    )  # fmt: skip

    day = start
    while day < end:
        by_steps = max(
            (step.accrued for step in steps if step.date <= day), default=Decimal("0.00")
        )
        direct = compute_accrual(
            hire_date=hire, as_of=day, annual_norm=norm, rounding_threshold_days=threshold,
            excluded_days=excluded, working_year_start=start,
        ).accrued  # fmt: skip
        assert by_steps == direct, f"{day}: шаги {by_steps}, compute_accrual {direct}"
        day += datetime.timedelta(days=1)


def test_steps_refuse_what_compute_accrual_refuses() -> None:
    with pytest.raises(ValueError, match="годовщиной"):
        accrual_steps(
            hire_date=HIRE, working_year_start=D(2026, 4, 1), as_of=D(2026, 6, 1), annual_norm=NORM,
            rounding_threshold_days=THRESHOLD, excluded_days=0,
        )  # fmt: skip
    with pytest.raises(ValueError, match="Порог"):
        accrual_steps(
            hire_date=HIRE, working_year_start=HIRE, as_of=D(2026, 6, 1), annual_norm=NORM,
            rounding_threshold_days=0, excluded_days=0,
        )  # fmt: skip
