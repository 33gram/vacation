"""Чистая арифметика накопления отпуска (T-M2-05, `FR-BAL-001/1`,
`FR-BAL-001/2`): без сессии и без БД, на входе только числа и даты.

Правила (источники и пометки ВЫБРАНО/ВЫВЕДЕНО — `requirements.md` v1.24,
раздел 13 п. 30):
- РАБОЧИЙ ГОД считается от даты приёма и её годовщин (ст. 121 ТК РФ), а не
  календарный. Конец года в результате — ИСКЛЮЧИТЕЛЬНО (первый день
  следующего года), как в журнале (`working_year_end > working_year_start`).
- Накопление — по ПОЛНЫМ рабочим месяцам от даты приёма: норма × месяцы /
  12. Остаток неполного месяца не меньше порога округляется до месяца, меньше
  — отбрасывается. Месячной константы нет: округляется только ИТОГ, до двух
  знаков, `Decimal`, `ROUND_HALF_UP`, без `float`.
- Границы месяцев — `начало + n месяцев`, считаются от даты приёма, а не
  накоплением по месяцу; если в целевом месяце нет такого дня (приём 31-го),
  берётся последний день месяца.
- ИСКЛЮЧАЕМЫЕ ДНИ сдвигают вперёд конец рабочего года и границы месяцев
  (из прошедшего времени вычитаются). Исключается не всё время без
  содержания, а только превышение суммы за рабочий год над лимитом
  (`excluded_unpaid_leave_days`).

Норма, порог и лимит — ПАРАМЕТРЫ вызова (`annual_leave_days`,
`month_rounding_threshold_days`, `unpaid_leave_days_counted_in_service` из
`business_rules_config`, правило 5), здесь нет ни 28, ни 14, ни 15. Норма по
категориям (удлинённый/дополнительный отпуск) здесь НЕ вычисляется: она
выводится за границы MVP (`A-3.1`, раздел «вне границ»), вызывающий
передаёт одну норму.

ГРАНИЦЫ (правило 8):
- Начало рабочего года — годовщина приёма. Сдвиг, который прежние
  исключения дали НАЧАЛУ следующих лет (начало года = конец предыдущего,
  сдвинутого), не моделируется: функция видит исключения ОДНОГО года.
- Дата в «хвосте» сдвинутого года (после годовщины, но до сдвинутого конца)
  по годовщинам попадает в следующий год. Чтобы получить прежний год,
  вызывающий передаёт `working_year_start` — начало того года; без него
  год определяется только годовщинами.
- Откуда берутся исключаемые дни (отпуска без содержания) — данные модуля
  заявок (M3); функция принимает число.
"""

from __future__ import annotations

import calendar
import datetime
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

MONTHS_IN_YEAR = 12
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class Accrual:
    """Результат расчёта на дату."""

    accrued: Decimal  # накоплено за рабочий год, два знака
    working_year_start: datetime.date
    working_year_end: datetime.date  # исключительно, с учётом сдвига
    full_months: int  # полных рабочих месяцев до округления остатка
    remainder_days: int  # дни неполного месяца
    counted_months: int  # месяцы после округления остатка (не более 12)


def _add_months(date: datetime.date, months: int) -> datetime.date:
    """`date` + `months` месяцев; если дня нет в целевом месяце — последний
    день месяца (31.01 + 1 мес = 28.02)."""
    index = date.year * MONTHS_IN_YEAR + (date.month - 1) + months
    year, month = divmod(index, MONTHS_IN_YEAR)
    day = min(date.day, calendar.monthrange(year, month + 1)[1])
    return datetime.date(year, month + 1, day)


def _anniversary(hire_date: datetime.date, years: int) -> datetime.date:
    return _add_months(hire_date, years * MONTHS_IN_YEAR)


def working_year_start_on(hire_date: datetime.date, year_index: int) -> datetime.date:
    """Начало `year_index`-го рабочего года (0 - первый): годовщина приёма."""
    return _anniversary(hire_date, year_index)


def working_year_containing(
    hire_date: datetime.date, on_date: datetime.date
) -> tuple[datetime.date, datetime.date]:
    """Рабочий год, в который попадает `on_date`: (начало, конец исключительно) по
    годовщинам приёма. Исключаемые дни не учитываются (границы - в докстринге
    модуля: дата в «хвосте» сдвинутого года относится к следующему году)."""
    if on_date < hire_date:
        raise ValueError("Дата раньше даты приёма.")
    start = working_year_starts(hire_date, on_date)[-1]
    year_index = start.year - hire_date.year
    return start, _anniversary(hire_date, year_index + 1)


def is_working_year_start(hire_date: datetime.date, start: datetime.date) -> bool:
    """`start` - начало какого-то рабочего года при данной дате приёма, то есть
    годовщина приёма (не раньше самой даты приёма)."""
    year_index = start.year - hire_date.year
    return year_index >= 0 and _anniversary(hire_date, year_index) == start


def working_year_starts(hire_date: datetime.date, as_of: datetime.date) -> list[datetime.date]:
    """Начала всех рабочих годов от приёма до `as_of` включительно (пусто, если
    `as_of` раньше приёма). Исключаемые дни не учитываются (начала лет - годовщины,
    см. границы в докстринге модуля)."""
    starts: list[datetime.date] = []
    year_index = 0
    while _anniversary(hire_date, year_index) <= as_of:
        starts.append(_anniversary(hire_date, year_index))
        year_index += 1
    return starts


def _boundary(hire_date: datetime.date, months: int) -> datetime.date:
    """Граница `months`-го месяца: `hire_date + months` месяцев, ВСЕГДА от
    даты приёма, а не цепочкой от предыдущей границы (приём 31.01: вторая
    граница 31.03, а не 28.02 + 1 месяц = 28.03)."""
    return _add_months(hire_date, months)


def _months_reached(hire_date: datetime.date, reached: datetime.date) -> int:
    """ЕДИНСТВЕННАЯ реализация границ месяцев: наибольшее `m >= 0`, при котором
    граница `m`-го месяца не позже `reached` (первый день, до которого время
    отработано, то есть последний отработанный день + 1). И `completed_months`,
    и `compute_accrual` считают месяцы только через неё."""
    calendar_months = (reached.year - hire_date.year) * MONTHS_IN_YEAR
    months = max(0, calendar_months + reached.month - hire_date.month)
    while months > 0 and _boundary(hire_date, months) > reached:
        months -= 1
    while _boundary(hire_date, months + 1) <= reached:
        months += 1
    return months


def completed_months(hire_date: datetime.date, as_of: datetime.date) -> int:
    """Полных рабочих месяцев от даты приёма на дату `as_of` включительно
    (стаж; не ограничено рабочим годом, в отличие от накопления). Месяц
    приёма 10.03 кончается 09.04 включительно: на 09.04 стаж 1 месяц.
    Используется предикатом «отпуск авансом» (`T-M2-06`). Границы месяцев
    даёт тот же примитив `_months_reached`, что и накопление, - второго
    счёта нет.

    ОТКРЫТЫЙ ВОПРОС (не решён): считается КАЛЕНДАРНАЯ выслуга от даты приёма;
    исключаемые периоды (отпуска без содержания сверх лимита, ст. 121) стаж
    не сдвигают. Шесть месяцев по ст. 122 - «стаж непрерывной работы»,
    поэтому, возможно, момент права на первый отпуск тоже должен сдвигаться
    ими. Считать ли по стажу с исключениями или по календарной выслуге, НЕ
    решено; сегодня календарная; решить вместе с появлением периодов без
    содержания (M3)."""
    if as_of < hire_date:
        raise ValueError("Дата расчёта раньше даты приёма.")
    return _months_reached(hire_date, as_of + datetime.timedelta(days=1))


@dataclass(frozen=True)
class AccrualStep:
    """Один шаг накопления: день, с которого накоплено `accrued`."""

    date: datetime.date
    counted_months: int  # месяцы после округления остатка на этот шаг, 1..12
    accrued: Decimal  # НАКОПЛЕНО на эту дату нарастающим итогом, два знака


def accrual_steps(
    *,
    hire_date: datetime.date,
    working_year_start: datetime.date,
    as_of: datetime.date,
    annual_norm: int | Decimal,
    rounding_threshold_days: int,
    excluded_days: int,
) -> list[AccrualStep]:
    """Шаги накопления рабочего года `working_year_start` до `as_of` включительно, по
    порядку: не больше двенадцати, по одному на месяц (`T-M2-21`). Шаг `k` - первый день,
    с которого `compute_accrual` считает `k` месяцев: остаток неполного месяца достиг
    порога (`граница(k-1) + порог дней`) либо, если порог длиннее месяца, месяц стал
    полным (день перед `границей(k)`: месяц приёма 10.03 кончается 09.04 включительно).
    Даты ИЗМЕРЕНЫ перебором дней по `compute_accrual`, а не взяты из текста задачи.
    `excluded_days` сдвигает каждую дату на столько же дней, как сдвигает `compute_accrual`.

    `accrued` шага - нарастающий итог, округлённый тем же выражением, что в
    `compute_accrual`; количество, которое шаг добавляет, - разность соседних итогов
    (разность округлённых, а не округление разности: двенадцать по 2,33 не дали бы 28).

    ГАРАНТИРУЕТСЯ: для любого дня рабочего года накопленное по шагам с датой не позже
    дня равно `compute_accrual(as_of=день).accrued` (перебор дней в
    `test_accrual_arithmetic.py`; границы месяцев - тот же `_boundary`, что считает
    `compute_accrual`). НЕ гарантируется: вне года `[начало, конец + excluded_days)`
    функция не проверяет `as_of` (позже конца даст все двенадцать шагов, раньше начала -
    пусто), и сдвиг `working_year_start` предыдущими исключениями не моделируется
    (границы модуля). Тот же отказ `ValueError`, что у `compute_accrual`, на негодные
    параметры."""
    if excluded_days < 0:
        raise ValueError("Число исключаемых дней не может быть отрицательным.")
    if annual_norm < 0:
        raise ValueError("Норма отпуска не может быть отрицательной.")
    if rounding_threshold_days < 1:
        raise ValueError("Порог округления месяца должен быть не меньше 1 дня.")
    year_index = working_year_start.year - hire_date.year
    if year_index < 0 or _anniversary(hire_date, year_index) != working_year_start:
        raise ValueError("Начало рабочего года не является годовщиной даты приёма.")

    base = year_index * MONTHS_IN_YEAR
    shift = datetime.timedelta(days=excluded_days)
    steps: list[AccrualStep] = []
    for counted in range(1, MONTHS_IN_YEAR + 1):
        threshold_day = _boundary(hire_date, base + counted - 1) + datetime.timedelta(
            days=rounding_threshold_days
        )
        reached = min(threshold_day, _boundary(hire_date, base + counted))
        step_date = reached - datetime.timedelta(days=1) + shift
        if step_date > as_of:
            break
        steps.append(
            AccrualStep(
                date=step_date,
                counted_months=counted,
                accrued=(Decimal(annual_norm) * counted / MONTHS_IN_YEAR).quantize(
                    _CENT, rounding=ROUND_HALF_UP
                ),
            )
        )
    return steps


def excluded_unpaid_leave_days(leave_lengths: Sequence[int], counted_limit: int) -> int:
    """Сколько дней выпадает из стажа за рабочий год (ст. 121 ТК РФ):
    СУММА календарных дней отпусков без сохранения заработной платы за
    рабочий год минус лимит, не меньше нуля. Не «периоды подряд»: три
    отпуска по 10 дней при лимите 14 дают 16."""
    return max(0, sum(leave_lengths) - counted_limit)


def compute_accrual(
    *,
    hire_date: datetime.date,
    as_of: datetime.date,
    annual_norm: int | Decimal,
    rounding_threshold_days: int,
    excluded_days: int,
    working_year_start: datetime.date | None = None,
) -> Accrual:
    if as_of < hire_date:
        raise ValueError("Дата расчёта раньше даты приёма.")
    if excluded_days < 0:
        raise ValueError("Число исключаемых дней не может быть отрицательным.")
    if annual_norm < 0:
        raise ValueError("Норма отпуска не может быть отрицательной.")
    if rounding_threshold_days < 1:
        raise ValueError("Порог округления месяца должен быть не меньше 1 дня.")

    if working_year_start is None:
        year_index = as_of.year - hire_date.year
        while _anniversary(hire_date, year_index) > as_of:
            year_index -= 1
    else:
        year_index = working_year_start.year - hire_date.year
        if _anniversary(hire_date, year_index) != working_year_start:
            raise ValueError("Начало рабочего года не является годовщиной даты приёма.")
        if year_index < 0:
            raise ValueError("Рабочий год начинается раньше даты приёма.")
    start = _anniversary(hire_date, year_index)
    end = _anniversary(hire_date, year_index + 1) + datetime.timedelta(days=excluded_days)
    if not start <= as_of < end:
        raise ValueError("Дата расчёта вне указанного рабочего года.")

    # Прошедшее время без исключённых дней; `+ 1` - день расчёта отработан.
    reached = as_of - datetime.timedelta(days=excluded_days) + datetime.timedelta(days=1)
    base = year_index * MONTHS_IN_YEAR  # границы - от даты приёма, не от `start`
    months = min(MONTHS_IN_YEAR, max(0, _months_reached(hire_date, reached) - base))
    remainder = 0
    if months < MONTHS_IN_YEAR:
        remainder = max(0, (reached - _boundary(hire_date, base + months)).days)
    counted = min(MONTHS_IN_YEAR, months + (1 if remainder >= rounding_threshold_days else 0))

    accrued = (Decimal(annual_norm) * counted / MONTHS_IN_YEAR).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )
    return Accrual(
        accrued=accrued,
        working_year_start=start,
        working_year_end=end,
        full_months=months,
        remainder_days=remainder,
        counted_months=counted,
    )

