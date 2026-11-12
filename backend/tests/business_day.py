"""«Сегодня» тестов, которые идут через бизнес-день Системы (T-M2-34, `NFR-LOC-002`).

Эндпоинты и сервисы считают «сегодня» по параметру `business_timezone` (сидирование `0011`:
Europe/Moscow), а не по UTC. Тест, который строит даты от `utcnow().date()`, между 00:00 и
03:00 по Москве расходился бы с ними на сутки, поэтому такие тесты берут день отсюда. Часы
реальные (тесты, которым нужен ЗАДАННЫЙ момент, подменяют `utcnow` у бизнес-дня сами)."""

from __future__ import annotations

import datetime
import zoneinfo

from app.core.time_utils import business_date, utcnow

SEEDED_ZONE = zoneinfo.ZoneInfo("Europe/Moscow")


def seeded_business_today() -> datetime.date:
    return business_date(utcnow(), SEEDED_ZONE)
