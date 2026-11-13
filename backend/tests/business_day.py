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
её тесты - `tests/modules/business_rules/test_business_day.py`.

ГДЕ ПОМОЩНИК БЕССИЛЕН ПО ВРЕМЕНИ СУТОК. Помощник берёт реальные часы, поэтому вне окна 21:00-24:00
UTC (там дата по Москве уже следующая) UTC-день и день по Москве совпадают, и тесты, переведённые на
него, UTC-регрессию не замечают. ИЗМЕРЕНО (PR #196 review, П-3): мутант `business_today ->
utcnow().date()` в 08:08 UTC, `make test-backend`, - 10 красных и 1155 зелёных (61 пропущен), все
10 в трёх файлах, которые ЗАДАЮТ момент сами: `test_business_day_endpoints.py` (5),
`test_business_day.py` (4), `test_balance_recalculation_wiring.py` (1, тест с `22:00Z`); мутант
с реальными часами (`datetime.now(UTC).date()`) - 7 красных, тоже только там. Внутри окна по
часам реального прогона могли бы покраснеть и переведённые тесты; это НЕ измерялось. Держит
UTC-регрессию в любой час суток тест с фиксированным моментом 2026-11-12T22:00Z, пояс параметра
- Москва: `test_the_answer_names_the_server_business_day_and_the_default_date_is_it`
(`GET .../balances`, красный текст: `assert ('2026-11-12', '2026-11-12') ==
('2026-11-13', '2026-11-13')`)."""

from __future__ import annotations

import datetime
import zoneinfo

from app.core.time_utils import business_date, utcnow

SEEDED_ZONE = zoneinfo.ZoneInfo("Europe/Moscow")


def seeded_business_today() -> datetime.date:
    return business_date(utcnow(), SEEDED_ZONE)
