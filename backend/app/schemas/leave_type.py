"""Pydantic-схемы для `/api/leave-types*` (T-M2-10, FR-ADMIN-001, раздел 6 требований).

Схема отдаёт и принимает ВСЕ атрибуты раздела 6, включая «по виду ведётся остаток».
Наборы значений строковых атрибутов НЕ перепечатаны: берутся из модели
(`LEAVE_TYPE_*_VALUES`), той же, что строит `CheckConstraint`, - один набор, одно место.
`Literal[*...]` - и проверка, и перечисление в схеме OpenAPI (TS-клиент получает union).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.leave_type import (
    LEAVE_TYPE_CARRY_OVER_VALUES,
    LEAVE_TYPE_COUNTED_NON_WORKING_DAYS_VALUES,
    LEAVE_TYPE_PAID_VALUES,
    LEAVE_TYPE_UNIT_VALUES,
)

IsPaid = Literal[*LEAVE_TYPE_PAID_VALUES]
Unit = Literal[*LEAVE_TYPE_UNIT_VALUES]
CarryOverPolicy = Literal[*LEAVE_TYPE_CARRY_OVER_VALUES]
CountedNonWorkingDays = Literal[*LEAVE_TYPE_COUNTED_NON_WORKING_DAYS_VALUES]

# Код - часть контракта (на него ссылаются заявки, тесты, фронтенд и `MAIN_LEAVE_TYPE_CODE`):
# латиница, snake_case (миграция `0006`).
Code = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
# `Numeric(7, 2)` в модели: пять цифр до запятой, две после; NULL - лимита нет.
AnnualLimit = Annotated[Decimal, Field(ge=0, max_digits=7, decimal_places=2)] | None


class _LeaveTypeAttributes(BaseModel):
    """Атрибуты вида без кода. Ни у одного, кроме `annual_limit` (NULL - «лимита нет»),
    нет умолчания: раздел 6 умолчаний не задаёт, молча принять их значило бы выдумать
    требование."""

    name: Name
    is_paid: IsPaid
    unit: Unit
    reduces_main_balance: bool
    # Ведёт ли Система по виду остаток (раздел 6 v1.26); не то же, что предыдущий атрибут.
    balance_tracked: bool
    requires_document: bool
    requires_approval: bool
    annual_limit: AnnualLimit = None
    carry_over_policy: CarryOverPolicy
    counted_non_working_days: CountedNonWorkingDays
    is_active: bool


class LeaveTypeCreate(_LeaveTypeAttributes):
    code: Code


class LeaveTypeUpdate(_LeaveTypeAttributes):
    # Код не меняется (по нему находится основной вид, `MAIN_LEAVE_TYPE_CODE`). Поле
    # принимается, чтобы форма могла отправить объект целиком; ДРУГОЕ значение - отказ
    # (400), а не молчаливый игнор.
    code: Code | None = None


class LeaveTypeOut(_LeaveTypeAttributes):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
