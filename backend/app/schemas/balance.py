"""Схемы остатка отпуска (T-M2-11, `FR-BAL-002`, `FR-BAL-004`)."""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

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


class AccrualYearOut(BaseModel):
    """Начисления одного рабочего года, свёрнутые сервером (`T-M2-30`, `FR-BAL-002`):
    сумма событий `accrual` года на дату запроса. Это агрегат, а не событие: у него нет
    ни `id`, ни причины, ни автора, и в `history` он не входит. Клиент итог не считает,
    а показывает как пришёл. Год без событий `accrual` (например, только начальный
    остаток) в списке отсутствует."""

    working_year_start: datetime.date
    working_year_end: datetime.date
    accrued: Decimal  # сумма `accrual` года на дату запроса (без пересчётов и корректировок)
    last_accrual_date: datetime.date  # дата последнего начисления года, не позже даты запроса


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
    # События БЕЗ начислений, по возрастанию даты; начисления - в `accrual_years` (`T-M2-30`).
    history: list[BalanceEntryOut]
    accrual_years: list[AccrualYearOut]  # по возрастанию начала года


class EmployeeBalancesOut(BaseModel):
    employee_id: int
    as_of: datetime.date
    # Бизнес-день СЕРВЕРА (`NFR-LOC-002`): клиент берёт из него верхнюю границу выбора даты и не
    # считает «сегодня» по своим часам. Не то же, что `as_of` (дата показа, по умолчанию он же).
    today: datetime.date
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
            "Дата, на которую действует корректировка; по умолчанию бизнес-день (NFR-LOC-002). "
            "Не может быть в будущем: корректировка фиксирует уже случившееся (отказ 400)."
        ),
    )


class BalanceAdjustOut(BaseModel):
    leave_type_id: int
    working_year_start: datetime.date
    working_year_end: datetime.date
    balance: Decimal  # новый остаток за этот рабочий год
