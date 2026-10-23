"""Схемы остатка отпуска (T-M2-11, `FR-BAL-002`, `FR-BAL-004`)."""

from __future__ import annotations

import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class BalanceEntryOut(BaseModel):
    """Событие истории остатка. `actor_name` подставляет слой эндпоинта по
    `actor_account_id` (сервис `balances` имя не резолвит); `None` у события, которое
    начисляет система."""

    id: int
    entry_type: str
    quantity: Decimal
    entry_date: datetime.date
    working_year_start: datetime.date
    working_year_end: datetime.date
    reason: str | None
    actor_account_id: int | None
    actor_name: str | None

    model_config = {"from_attributes": True}


class LeaveTypeBalanceOut(BaseModel):
    """Остаток по одному виду (`FR-BAL-002`)."""

    leave_type_id: int
    code: str
    name: str
    unit: str
    # Рабочий год, в который попадает дата запроса; `None` - дата раньше приёма.
    working_year_start: datetime.date | None
    working_year_end: datetime.date | None
    balance: Decimal  # остаток за этот рабочий год на дату запроса
    total_balance: Decimal  # сумма по ВСЕМ рабочим годам на дату запроса
    history: list[BalanceEntryOut]


class EmployeeBalancesOut(BaseModel):
    employee_id: int
    as_of: datetime.date
    balances: list[LeaveTypeBalanceOut]


class BalanceAdjustRequest(BaseModel):
    """Ручная корректировка (`FR-BAL-004`). `reason` - обязательное поле схемы;
    пустая строка и одни пробелы отклоняются сервисом. Рабочий год клиент не
    присылает: его определяет модуль `balances` по `entry_date`."""

    leave_type_id: int
    quantity: Decimal = Field(max_digits=7, decimal_places=2)
    reason: str
    entry_date: datetime.date | None = None


class BalanceAdjustOut(BaseModel):
    leave_type_id: int
    working_year_start: datetime.date
    working_year_end: datetime.date
    balance: Decimal  # новый остаток за этот рабочий год
