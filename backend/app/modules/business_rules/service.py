"""Чтение и проверка параметров бизнес-правил (T-M2-09, `FR-ADMIN-004`,
`NFR-MAINT-001`).

Единственная точка, через которую остальной код получает значение из
`business_rules_config`. Таблица хранит СТРОКУ в формате, объявленном
`value_type` (см. миграцию `0007`); смысл — тип, диапазон, допустимость —
придаёт реестр `RULES` ниже.

КАК ЭТО РАБОТАЕТ
- Параметр читается ЗАПРОСОМ К БАЗЕ при каждом обращении, без кэша в
  процессе. Причина: приложение и воркеры — несколько процессов, а
  инвалидировать кэш одного процесса из другого нечем; кэш с TTL означал
  бы, что правка администратора (`T-M2-12`) вступает в силу «когда-нибудь»,
  а не со следующего запроса. Цена — один индексированный запрос по
  `key IN (...)` на вход/смену пароля/расчёт, при ≤ 500 сотрудниках
  несущественная. После правки администратором следующее чтение видит
  новое значение; запрос, уже прочитавший старое, доработает со старым.
  Запрос читает свои ключи ОДНИМ обращением (`get_rules`).
- Негодное значение — НЕ молчаливая подстановка: `BusinessRuleError` с
  ключом. Значения `DEFAULT_*` в коде остались только источником
  сидированного значения (проверяется тестом) и рантайм-фолбэком не
  служат: молчаливый откат на значение из кода — ровно тот способ, каким
  бизнес-правило незаметно расходится с настройкой.
- Отсутствующий или негодный ключ ловится при СТАРТЕ приложения
  (`validate_all` в `lifespan`): отказ старта с перечнем ВСЕХ проблем
  лучше, чем сервис, поднявшийся и падающий по одному запросу на каждый
  ключ — человек, разворачивающий систему, видит всё сразу, до первого
  пользователя. Рантайм-проверка при чтении остаётся: строка могла быть
  испорчена после старта.
- Запись параметра (`T-M2-12`) обязана проверять значение тем же
  `parse_stored_value`, а не отдельной логикой, иначе запись и чтение
  разойдутся в понимании допустимого.

ГРАНИЦЫ. Строка в таблице, которой нет в `RULES`, читателем не видна и
стартовой проверкой не охватывается: реестр — единственное место, где у
ключа есть диапазон. Добавление параметра = строка в таблице + запись в
реестре; схема не меняется.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import BCRYPT_MAX_INPUT_BYTES
from app.models.business_rule_config import INTEGER, MONTH_DAY, BusinessRuleConfig

# Имена ключей — константами, чтобы опечатка в строке не превращалась в
# «ключ отсутствует» только в рантайме.
ANNUAL_LEAVE_DAYS = "annual_leave_days"
FIRST_LEAVE_ELIGIBILITY_MONTHS = "first_leave_eligibility_months"
SCHEDULE_APPROVAL_DEADLINE = "schedule_approval_deadline"
VACATION_START_NOTICE_DAYS = "vacation_start_notice_days"
MIN_LEAVE_PART_DAYS = "min_leave_part_days"
LEAVE_USAGE_DEADLINE_MONTHS = "leave_usage_deadline_months"
COMPENSATION_THRESHOLD_DAYS = "compensation_threshold_days"
APPROVAL_ESCALATION_WORKING_DAYS = "approval_escalation_working_days"
PASSWORD_MIN_LENGTH = "password_min_length"
MAX_FAILED_LOGIN_ATTEMPTS = "max_failed_login_attempts"
LOCKOUT_DURATION_MINUTES = "lockout_duration_minutes"


class BusinessRuleError(Exception):
    """Параметр отсутствует или его значение недопустимо. Несёт ключ."""

    def __init__(self, key: str, problem: str) -> None:
        super().__init__(f"business rule {key!r}: {problem}")
        self.key = key
        self.problem = problem


class BusinessRulesConfigError(RuntimeError):
    """Стартовая проверка нашла проблемы; перечисляет все, не первую."""

    def __init__(self, problems: list[BusinessRuleError]) -> None:
        keys = ", ".join(problem.key for problem in problems)
        details = "; ".join(str(problem) for problem in problems)
        super().__init__(f"business_rules_config is not usable (keys: {keys}): {details}")
        self.problems = problems


@dataclass(frozen=True)
class MonthDay:
    """Календарная дата без года (`ММ-ДД`). `02-29` недопустима: ежегодный
    срок, которого нет в большинстве лет, параметром быть не может."""

    month: int
    day: int

    def on(self, year: int) -> datetime.date:
        return datetime.date(year, self.month, self.day)


@dataclass(frozen=True)
class RuleSpec:
    """Что реестр знает о ключе: тип значения и допустимый диапазон
    (для `integer` — закрытый интервал). Диапазоны — ВЫБРАНО, кроме
    названных в комментариях: требования задают умолчания, а не границы."""

    key: str
    value_type: str
    minimum: int | None = None
    maximum: int | None = None


# Реестр. Границы: «дни в году» — не более 366; месяцы — не более 5/10
# лет; остальное — ВЫБРАНО как заведомо разумный предел, за которым
# значение — почти наверняка опечатка (например, лишний ноль).
# Исключения с источником:
#  - PASSWORD_MIN_LENGTH: 8..72 задано критерием FR-AUTH-002/15 (v1.23,
#    решение владельца продукта, раздел 13 п. 29): нижняя граница 8 —
#    значение по умолчанию, политику разрешено усиливать, но не ослаблять;
#    верхняя — предел bcrypt в 72 байта (FR-AUTH-002/4,
#    `BCRYPT_MAX_INPUT_BYTES`): минимум длиннее предела сделал бы
#    невозможным любой пароль. Это необходимое, но не достаточное условие:
#    минимум в 72 символа кириллицы не пройдёт по байтам — такой пароль
#    политика отклонит, а не параметр.
RULES: dict[str, RuleSpec] = {
    spec.key: spec
    for spec in (
        RuleSpec(ANNUAL_LEAVE_DAYS, INTEGER, 1, 366),
        RuleSpec(FIRST_LEAVE_ELIGIBILITY_MONTHS, INTEGER, 0, 60),
        RuleSpec(SCHEDULE_APPROVAL_DEADLINE, MONTH_DAY),
        RuleSpec(VACATION_START_NOTICE_DAYS, INTEGER, 0, 366),
        RuleSpec(MIN_LEAVE_PART_DAYS, INTEGER, 1, 366),
        RuleSpec(LEAVE_USAGE_DEADLINE_MONTHS, INTEGER, 1, 120),
        RuleSpec(COMPENSATION_THRESHOLD_DAYS, INTEGER, 1, 366),
        RuleSpec(APPROVAL_ESCALATION_WORKING_DAYS, INTEGER, 1, 60),
        RuleSpec(PASSWORD_MIN_LENGTH, INTEGER, 8, BCRYPT_MAX_INPUT_BYTES),
        RuleSpec(MAX_FAILED_LOGIN_ATTEMPTS, INTEGER, 1, 100),
        RuleSpec(LOCKOUT_DURATION_MINUTES, INTEGER, 1, 7 * 24 * 60),
    )
}

RuleValue = int | MonthDay


def parse_stored_value(key: str, value: str, value_type: str) -> RuleValue:
    """Приводит строку из таблицы к типу, проверяя тип, формат и диапазон.
    Для ключа, которого нет в реестре, — отказ: диапазона у него нет."""
    spec = RULES.get(key)
    if spec is None:
        raise BusinessRuleError(key, "unknown key (not in the rules registry)")
    if value_type != spec.value_type:
        raise BusinessRuleError(
            key, f"stored value_type {value_type!r}, the registry expects {spec.value_type!r}"
        )
    if spec.value_type == INTEGER:
        if not (value.isascii() and value.isdigit()):
            raise BusinessRuleError(key, f"{value!r} is not a non-negative integer")
        number = int(value)
        # Не `assert`: под `python -O` он исчезает, и защита существовала бы
        # только в отладочном режиме.
        if spec.minimum is None or spec.maximum is None:
            raise BusinessRuleError(key, "the registry has no range for this integer rule")
        if not spec.minimum <= number <= spec.maximum:
            raise BusinessRuleError(
                key, f"{number} is outside the allowed range {spec.minimum}..{spec.maximum}"
            )
        return number
    # Формат — тот же, что у `CHECK` базы (`[0-9]{2}-[0-9]{2}`, только ASCII):
    # `int()` и `date()` принимают `1-5`, ` 12-17`, `+1-+5` и арабо-индийские
    # цифры, которые база отвергнет, — сервис не должен быть мягче неё.
    # `fullmatch`, а не `$`: `$` пропускает завершающий перевод строки.
    if not re.fullmatch(r"[0-9]{2}-[0-9]{2}", value):
        raise BusinessRuleError(key, f"{value!r} is not a valid MM-DD date")
    month, _, day = value.partition("-")
    try:
        # 2001 — не високосный: 02-29 отвергается, см. `MonthDay`.
        datetime.date(2001, int(month), int(day))
    except ValueError:
        raise BusinessRuleError(key, f"{value!r} is not a valid MM-DD date") from None
    return MonthDay(int(month), int(day))


def _resolve(key: str, stored: dict[str, Any]) -> RuleValue:
    """Строка ключа из прочитанных -> значение. Общий разбор для чтения и
    стартовой проверки: два места не должны расходиться в понимании
    «отсутствует» и «негоден»."""
    row = stored.get(key)
    if row is None:
        raise BusinessRuleError(key, "missing from business_rules_config")
    return parse_stored_value(key, row.value, row.value_type)


async def get_rules(session: AsyncSession, *keys: str) -> dict[str, RuleValue]:
    """Читает несколько параметров ОДНИМ запросом."""
    result = await session.execute(
        select(
            BusinessRuleConfig.key, BusinessRuleConfig.value, BusinessRuleConfig.value_type
        ).where(BusinessRuleConfig.key.in_(keys))
    )
    stored = {row.key: row for row in result}
    return {key: _resolve(key, stored) for key in keys}


async def get_int(session: AsyncSession, key: str) -> int:
    value = (await get_rules(session, key))[key]
    # Не `assert`: под `python -O` он исчезает (см. `parse_stored_value`).
    if not isinstance(value, int):
        raise BusinessRuleError(key, "is not an integer rule")
    return value


async def get_month_day(session: AsyncSession, key: str) -> MonthDay:
    value = (await get_rules(session, key))[key]
    if not isinstance(value, MonthDay):
        raise BusinessRuleError(key, "is not a month_day rule")
    return value


async def validate_all(session: AsyncSession) -> None:
    """Стартовая проверка: каждый ключ реестра существует и разбирается.
    Собирает ВСЕ проблемы и отказывает одним `BusinessRulesConfigError`."""
    result = await session.execute(
        select(BusinessRuleConfig.key, BusinessRuleConfig.value, BusinessRuleConfig.value_type)
    )
    stored = {row.key: row for row in result}
    problems: list[BusinessRuleError] = []
    for key in RULES:
        try:
            _resolve(key, stored)
        except BusinessRuleError as error:
            problems.append(error)
    if problems:
        raise BusinessRulesConfigError(problems)
