"""Бизнес-день - «сегодня» Системы (T-M2-34, `NFR-LOC-002`, `BR-12`).

Единственное место БЭКЕНДА, которое читает пояс организации и отвечает на вопрос «какое число»
(сегодня - `business_today`, у произвольной метки - `business_date_of`): начисление отпуска
и остаток на дату по умолчанию (`GET .../balances`), граница даты корректировки и начисление перед
ней (`POST .../balances/adjust`), пересчёт при смене даты приёма. Пояс - параметр
`business_timezone` (по умолчанию Europe/Moscow, сидирование миграции `0011`); читатель при его
отсутствии отказывает, запасного значения в коде нет. Пояс контейнера и сервера в расчёте не
участвует. Клиент получает день этой же функцией через `GET /api/auth/me` (`today`) и ответ остатка
(`T-M2-34`, `T-M2-36`, `T-M2-38`): `EmployeeCardPage` и `ProductionCalendarPage` читают день сессии.
Вне этого модуля на бэкенде остаётся пояс планировщика Celery (`settings.tz`, `T-M2-39`);
`card_date` в `balances/service.py` (дата метки создания карточки) читается `business_date_of`
(`T-M2-40`).

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


async def business_date_of(session: AsyncSession, moment: datetime.datetime) -> datetime.date:
    """Календарная дата ПРОИЗВОЛЬНОЙ метки времени в поясе организации (`T-M2-40`): сохранённая
    метка (`created_at` и подобные, в UTC) читается как дата ЭТИМ же читателем параметра, а не
    переводом в UTC и не вторым чтением параметра в другом модуле. Момент обязан быть с поясом
    (`business_date` отказывает наивному)."""
    return business_date(moment, await get_timezone(session, BUSINESS_TIMEZONE))


async def business_today(session: AsyncSession) -> datetime.date:
    """Бизнес-день на этот момент: текущее UTC-время, переведённое в пояс организации."""
    return await business_date_of(session, utcnow())
