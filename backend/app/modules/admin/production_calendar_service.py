"""Производственный календарь (T-M1-17, FR-ADMIN-003)."""

from __future__ import annotations

import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.production_calendar_day import ProductionCalendarDay
from app.schemas.production_calendar import ProductionCalendarDayIn


async def get_year(session: AsyncSession, year: int) -> list[ProductionCalendarDay]:
    start = datetime.date(year, 1, 1)
    end = datetime.date(year, 12, 31)
    result = await session.execute(
        select(ProductionCalendarDay)
        .where(ProductionCalendarDay.date >= start, ProductionCalendarDay.date <= end)
        .order_by(ProductionCalendarDay.date)
    )
    return list(result.scalars().all())


async def replace_year(
    session: AsyncSession, year: int, days: list[ProductionCalendarDayIn]
) -> list[ProductionCalendarDay]:
    """Полная замена справочника на год: производственный календарь
    загружается целиком на год вперёд (T-M8-03), а не редактируется по
    дням, поэтому `PUT` заменяет весь год, а не мёржит его."""
    for day in days:
        if day.date.year != year:
            raise ValueError(f"date {day.date} does not belong to year {year}")

    start = datetime.date(year, 1, 1)
    end = datetime.date(year, 12, 31)
    await session.execute(
        delete(ProductionCalendarDay).where(
            ProductionCalendarDay.date >= start, ProductionCalendarDay.date <= end
        )
    )
    session.add_all(
        [ProductionCalendarDay(date=day.date, day_type=day.day_type) for day in days]
    )
    await session.commit()
    return await get_year(session, year)
