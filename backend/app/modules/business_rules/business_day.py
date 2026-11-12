"""Бизнес-день - «сегодня» Системы (T-M2-34, `NFR-LOC-002`, `BR-12`).

Единственное место, которое отвечает на вопрос «какое сегодня число»: начисление отпуска и
остаток на дату по умолчанию (`GET .../balances`), граница даты корректировки и начисление
перед ней (`POST .../balances/adjust`), пересчёт при смене даты приёма. Пояс - параметр
`business_timezone` (по умолчанию Europe/Moscow, сидирование миграции `0011`); читатель при его
отсутствии отказывает, запасного значения в коде нет. Пояс контейнера и сервера в расчёте не
участвует.

Метки времени (`utcnow()`: токены, блокировки, `updated_at`, журнал аудита) остаются в UTC и
бизнес-днём не являются - граница описана в `core.time_utils.business_date`.

ГРАНИЦЫ. (1) Параметр читается запросом к базе при каждом обращении (как весь реестр): правка
администратора действует со следующего запроса, запрос, уже узнавший день, доработает со
старым. (2) Смена пояса меняет, какой день считается сегодняшним, но уже записанные события
журнала не переписываются: событие, датированное по старому поясу, остаётся на своей дате.
"""

from __future__ import annotations

import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time_utils import business_date, utcnow
from app.modules.business_rules.service import BUSINESS_TIMEZONE, get_timezone


async def business_today(session: AsyncSession) -> datetime.date:
    """Бизнес-день на этот момент: текущее UTC-время, переведённое в пояс организации."""
    return business_date(utcnow(), await get_timezone(session, BUSINESS_TIMEZONE))
