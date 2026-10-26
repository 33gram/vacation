"""Схемы `/api/admin/business-rules` (T-M2-12, `FR-ADMIN-004`)."""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field


class BusinessRuleOut(BaseModel):
    """Параметр реестра. `value` - строка в формате `value_type` (как в таблице и как её
    принимает `PUT`): `integer` - десятичное целое, `month_day` - `ММ-ДД`.

    `minimum`/`maximum` - допустимый диапазон ИЗ РЕЕСТРА (`RuleSpec`), то есть то, что проверяет
    и запись, и чтение; у `month_day` диапазона нет - там `null`. Диапазон отдаётся ДЛЯ
    интерфейса `T-M2-15`; решает всё равно сервер."""

    key: str
    value: str
    value_type: str
    description: str
    minimum: int | None
    maximum: int | None
    updated_at: datetime.datetime
    # `null` - значение засеяно миграцией и ни разу не менялось через интерфейс.
    updated_by_name: str | None


class BusinessRulesUpdate(BaseModel):
    """Набор «ключ - значение» (форма параметров правится целиком). Значения - СТРОКИ в
    формате параметра (`"28"`, `"12-17"`); число JSON отклоняется схемой (422)."""

    values: dict[str, str] = Field(min_length=1)
