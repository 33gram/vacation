"""«Сегодня» тестов, которые идут через бизнес-день Системы (T-M2-34, `NFR-LOC-002`).

Эндпоинты и сервисы считают «сегодня» по параметру `business_timezone` (сидирование `0011`:
Europe/Moscow), а не по UTC. Тест, который строит даты от `utcnow().date()`, между 00:00 и
03:00 по Москве расходился бы с ними на сутки, поэтому такие тесты берут день отсюда. Часы
реальные (тесты, которым нужен ЗАДАННЫЙ момент, подменяют `utcnow` у бизнес-дня сами).

ЧТО ПОМОЩНИК ЛОВИТ И ЧЕГО НЕТ. Зона в нём своя (`Europe/Moscow` задана здесь, не читается из
базы), поэтому он ловит рабочий код, который прочитал не тот параметр или взял UTC-день. Но он
зовёт ТУ ЖЕ `business_date`, что и рабочий код: дефект внутри самой `business_date` (например,
перевод не по правилам зоны) сдвинул бы ожидание и код вместе, и тесты, которые берут день
отсюда (`test_balance_endpoints.py`, `test_balance_recalculation_wiring.py`, сценарий
`test_concurrent_org_changes.py`), остались бы зелёными. Саму `business_date` проверяют только
её тесты - `tests/modules/business_rules/test_business_day.py`."""

from __future__ import annotations

import datetime
import zoneinfo

from app.core.time_utils import business_date, utcnow

SEEDED_ZONE = zoneinfo.ZoneInfo("Europe/Moscow")


def seeded_business_today() -> datetime.date:
    return business_date(utcnow(), SEEDED_ZONE)
