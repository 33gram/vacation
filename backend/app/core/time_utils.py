from __future__ import annotations

import datetime
import zoneinfo


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def business_date(moment: datetime.datetime, zone: zoneinfo.ZoneInfo) -> datetime.date:
    """Календарная дата момента в часовом поясе `zone` - БИЗНЕС-ДЕНЬ (`NFR-LOC-002`).

    ГРАНИЦА между «меткой времени» и «бизнес-датой»: метки (токены, блокировки, `updated_at`,
    журнал аудита) берутся из `utcnow()` и остаются в UTC; календарная дата, от которой
    считаются начисление, остаток на дату, граница корректировки, - это ЭТА функция с поясом
    организации (параметр `business_timezone`, читатель - `business_rules.business_day`).
    Перевод идёт по правилам зоны (`astimezone`), а не сдвигом на фиксированные часы: у зоны с
    летним временем смещение зависит от даты. Момент обязан быть с поясом: наивное время
    `astimezone` трактует как ЛОКАЛЬНОЕ время процесса, то есть результат зависел бы от пояса
    контейнера, а расчёт от него не зависит."""
    if moment.tzinfo is None:
        raise ValueError("business_date needs an aware datetime, got a naive one")
    return moment.astimezone(zone).date()
