"""Предикат «отпуск авансом» (T-M2-06, третий критерий `FR-BAL-001`).

Граница: предикат решает «аванс или нет»; применение при создании заявки —
задача M3 (названа в `T-M2-06`). Здесь проверяется только решение.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from app.modules.balances.accrual import completed_months
from app.modules.balances.advance import is_advance_leave

D = datetime.date
HIRE = D(2026, 3, 10)


def _advance(as_of: datetime.date, requested: str, accrued: str) -> bool:
    return is_advance_leave(
        hire_date=HIRE,
        as_of=as_of,
        requested_days=Decimal(requested),
        accrued_days=Decimal(accrued),
        eligibility_months=6,
    )


def test_fr_bal_001_tenure_below_six_months_and_over_the_accrued_is_an_advance() -> None:
    assert _advance(D(2026, 6, 1), requested="10", accrued="7.00") is True


def test_fr_bal_001_tenure_below_six_months_within_the_accrued_is_not_an_advance() -> None:
    assert _advance(D(2026, 6, 1), requested="7.00", accrued="7.00") is False
    assert _advance(D(2026, 6, 1), requested="5", accrued="7.00") is False


def test_fr_bal_001_tenure_of_six_months_with_a_request_over_accrued_is_not_an_advance() -> None:
    # Ст. 122: право на отпуск за первый год целиком по истечении шести
    # месяцев; требование ужесточать нельзя. Шестой месяц кончается 09.09.
    assert _advance(D(2026, 9, 9), requested="28", accrued="14.00") is False


def test_fr_bal_001_the_last_day_before_six_months_complete_is_still_an_advance() -> None:
    assert completed_months(HIRE, D(2026, 9, 8)) == 5
    assert _advance(D(2026, 9, 8), requested="28", accrued="14.00") is True


def test_fr_bal_001_the_eligibility_term_is_a_parameter() -> None:
    assert (
        is_advance_leave(
            hire_date=HIRE,
            as_of=D(2026, 9, 9),
            requested_days=28,
            accrued_days=14,
            eligibility_months=12,
        )
        is True
    )


def test_completed_months_counts_from_the_hire_date_not_calendar_months() -> None:
    assert completed_months(HIRE, HIRE) == 0
    assert completed_months(HIRE, D(2026, 4, 8)) == 0
    assert completed_months(HIRE, D(2026, 4, 9)) == 1
    assert completed_months(HIRE, D(2027, 3, 9)) == 12
    assert completed_months(HIRE, D(2027, 4, 9)) == 13  # не ограничено рабочим годом
