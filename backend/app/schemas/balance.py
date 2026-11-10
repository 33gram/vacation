"""Схемы остатка отпуска (T-M2-11, `FR-BAL-002`, `FR-BAL-004`)."""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.time_utils import utcnow
from app.models.leave_balance_entry import LEAVE_BALANCE_ENTRY_TYPES
from app.schemas.leave_type import Unit

# Выведен из того же кортежа, что и `CheckConstraint` таблицы: источник значений один, схема
# OpenAPI получает перечисление (TS-клиент - union). Приём как в `schemas/leave_type.py`.
EntryType = Literal[*LEAVE_BALANCE_ENTRY_TYPES]


class BalanceEntryOut(BaseModel):
    """Событие истории остатка. `actor_name` - ТОЛЬКО подпись автора: идентификатор
    аккаунта в ответ не отдаётся (внутренний). «Система» - начисляет система; ФИО -
    автор с карточкой; без карточки - нейтральная подпись («Администратор»,
    «Пользователь системы»), логин не показывается."""

    id: int
    entry_type: EntryType
    quantity: Decimal
    entry_date: datetime.date
    working_year_start: datetime.date
    working_year_end: datetime.date
    reason: str | None
    actor_name: str

    model_config = {"from_attributes": True}


class LeaveTypeBalanceOut(BaseModel):
    """Остаток по одному виду (`FR-BAL-002`)."""

    leave_type_id: int
    code: str
    name: str
    unit: Unit
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
    entry_date: datetime.date | None = Field(
        default=None,
        description=(
            "Дата, на которую действует корректировка; по умолчанию сегодня (UTC). Не может "
            "быть в будущем: корректировка фиксирует уже случившееся."
        ),
    )

    @field_validator("entry_date")
    @classmethod
    def _not_in_the_future(cls, value: datetime.date | None) -> datetime.date | None:
        if value is not None and value > utcnow().date():
            raise ValueError("Дата корректировки не может быть в будущем.")
        return value


class BalanceAdjustOut(BaseModel):
    leave_type_id: int
    working_year_start: datetime.date
    working_year_end: datetime.date
    balance: Decimal  # новый остаток за этот рабочий год
