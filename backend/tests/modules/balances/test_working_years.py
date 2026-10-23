"""Границы рабочих годов (`accrual.py`, T-M2-07): прямые тесты помощников, на
которых стоит пересчёт. Ожидания посчитаны на бумаге."""

from __future__ import annotations

import datetime

from app.modules.balances.accrual import (
    is_working_year_start,
    working_year_start_on,
    working_year_starts,
)

D = datetime.date


def test_the_anniversary_of_a_29_february_hire_is_the_28th_in_common_years() -> None:
    hire = D(2024, 2, 29)

    assert working_year_start_on(hire, 0) == D(2024, 2, 29)
    assert working_year_start_on(hire, 1) == D(2025, 2, 28)
    assert working_year_start_on(hire, 4) == D(2028, 2, 29)  # снова високосный


def test_is_working_year_start_for_a_29_february_hire() -> None:
    hire = D(2024, 2, 29)

    assert is_working_year_start(hire, D(2025, 2, 28)) is True
    assert is_working_year_start(hire, D(2025, 3, 1)) is False
    assert is_working_year_start(hire, D(2024, 2, 29)) is True  # сама дата приёма


def test_is_working_year_start_for_a_hire_on_the_31st() -> None:
    hire = D(2026, 1, 31)

    assert is_working_year_start(hire, D(2027, 1, 31)) is True
    assert is_working_year_start(hire, D(2027, 1, 30)) is False


def test_a_start_before_the_hire_date_is_not_a_working_year_start() -> None:
    assert is_working_year_start(D(2026, 3, 10), D(2025, 3, 10)) is False


def test_working_year_starts_includes_the_anniversary_itself() -> None:
    hire = D(2024, 2, 29)

    assert working_year_starts(hire, D(2025, 2, 28)) == [D(2024, 2, 29), D(2025, 2, 28)]
    assert working_year_starts(hire, D(2025, 2, 27)) == [D(2024, 2, 29)]


def test_working_year_starts_on_the_hire_date_is_the_first_year_only() -> None:
    assert working_year_starts(D(2026, 3, 10), D(2026, 3, 10)) == [D(2026, 3, 10)]


def test_working_year_starts_before_the_hire_date_is_empty() -> None:
    assert working_year_starts(D(2026, 3, 10), D(2026, 3, 9)) == []
