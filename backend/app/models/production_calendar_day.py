from __future__ import annotations

import datetime

from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

DAY_TYPES = ("working", "weekend", "holiday")


class ProductionCalendarDay(Base):
    __tablename__ = "production_calendar_days"
    __table_args__ = (
        CheckConstraint(
            "day_type IN ('working', 'weekend', 'holiday')",
            name="ck_production_calendar_days_day_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[datetime.date] = mapped_column(unique=True, nullable=False)
    day_type: Mapped[str] = mapped_column(nullable=False)
