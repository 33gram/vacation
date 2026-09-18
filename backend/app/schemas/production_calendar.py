"""Pydantic-схемы для `/api/admin/production-calendar/{year}` (T-M1-17)."""

from __future__ import annotations

import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

DayType = Literal["working", "weekend", "holiday"]


class ProductionCalendarDayIn(BaseModel):
    date: datetime.date
    day_type: DayType


class ProductionCalendarDayOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: datetime.date
    day_type: str


class ProductionCalendarYearIn(BaseModel):
    days: list[ProductionCalendarDayIn]


class ProductionCalendarYearOut(BaseModel):
    year: int
    days: list[ProductionCalendarDayOut]
