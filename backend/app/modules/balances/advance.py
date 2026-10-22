"""Предикат «отпуск авансом» (T-M2-06, третий критерий `FR-BAL-001`).

Критерий: WHILE стаж менее 6 месяцев, THE SYSTEM SHALL помечать использование
дней сверх пропорционально накопленных как «отпуск авансом» и требовать
подтверждения HR.

ГРАНИЦА (правило 8). Критерий наступает в момент ПОПЫТКИ использовать дни
(создание заявки), а модуля заявок ещё нет. Здесь только чистое решение
«аванс или нет»; пометка заявки и требование подтверждения HR — в сервисе
создания заявки (M3, задача названа в `T-M2-06`). Предикат не проверяет
остаток сам: накопленные дни вызывающий получает из `get_balance`.

Строго по критерию: при стаже, достигшем срока первого отпуска, предикат
ЛОЖЕН даже когда запрошено больше накопленного — ст. 122 ТК РФ даёт право
на отпуск за первый год целиком по истечении шести месяцев, и ужесточать
требование нельзя. Срок (6) — параметр `first_leave_eligibility_months`,
здесь его значение передаётся аргументом.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from app.modules.balances.accrual import completed_months


def is_advance_leave(
    *,
    hire_date: datetime.date,
    as_of: datetime.date,
    requested_days: Decimal | int,
    accrued_days: Decimal | int,
    eligibility_months: int,
) -> bool:
    """Истина — отпуск авансом, нужно подтверждение HR: стаж на `as_of`
    меньше `eligibility_months` полных месяцев И запрошено больше
    накопленного."""
    return (
        completed_months(hire_date, as_of) < eligibility_months
        and Decimal(requested_days) > Decimal(accrued_days)
    )
