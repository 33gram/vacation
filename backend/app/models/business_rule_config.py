from __future__ import annotations

import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# `integer` — десятичное целое без знака; `month_day` — дата без года `ММ-ДД`; `timezone` — имя
# часовой зоны IANA (`T-M2-34`, миграция `0011`). Новый тип значения — единственный случай,
# требующий миграции (CHECK ниже).
INTEGER = "integer"
MONTH_DAY = "month_day"
TIMEZONE = "timezone"
BUSINESS_RULE_VALUE_TYPES = (INTEGER, MONTH_DAY, TIMEZONE)

# ФОРМА имени зоны, которую проверяет CHECK базы: ASCII, сегменты через `/`, внутри буквы, цифры,
# `_`, `+`, `-` (`Europe/Moscow`, `UTC`, `Etc/GMT+3`, `America/Argentina/Buenos_Aires`). Это
# форма, а не годность: существует ли зона, база знать не может, это проверяет сервис через
# `zoneinfo` (`parse_stored_value`); сервис проверяет ту же форму тем же шаблоном ДО обращения к
# `zoneinfo`, поэтому он не мягче базы. Второе место шаблона - миграция `0011` (она не
# импортирует код приложения); совпадение двух мест держит тест `test_timezone_rule.py`.
TIMEZONE_FORM = r"^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*$"


class BusinessRuleConfig(Base):
    """Параметры бизнес-правил раздела 5 требований, key-value
    (T-M2-03, `FR-ADMIN-004`, `NFR-MAINT-001`).

    `value` — СТРОКА в формате, объявленном `value_type`; формат проверяет
    CHECK базы, диапазон и смысл — сервис чтения (`T-M2-09`). Единица
    измерения — часть имени ключа (`..._days`, `..._minutes`). Подробнее и
    почему не «всё целыми» — в докстринге миграции `0007`.

    Ни у одной колонки нет значения по умолчанию, кроме `updated_at`:
    значение параметра — решение, которое схема не выдумывает; сидирование
    — в миграции, а читатель при отсутствии ключа отказывает, а не
    подставляет значение из кода.
    """

    __tablename__ = "business_rules_config"
    __table_args__ = (
        CheckConstraint(
            "key ~ '^[a-z][a-z0-9_]*$'",
            name="ck_business_rules_config_key_format",
        ),
        CheckConstraint(
            "value_type IN (" + ", ".join(repr(t) for t in BUSINESS_RULE_VALUE_TYPES) + ")",
            name="ck_business_rules_config_value_type",
        ),
        CheckConstraint(
            "value_type <> 'integer' OR value ~ '^[0-9]+$'",
            name="ck_business_rules_config_integer_format",
        ),
        CheckConstraint(
            "value_type <> 'month_day' " "OR value ~ '^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$'",
            name="ck_business_rules_config_month_day_format",
        ),
        CheckConstraint(
            f"value_type <> 'timezone' OR value ~ '{TIMEZONE_FORM}'",
            name="ck_business_rules_config_timezone_format",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(unique=True, nullable=False)
    value: Mapped[str] = mapped_column(nullable=False)
    value_type: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str] = mapped_column(nullable=False)
    # NULL — значение сидировано миграцией и ни разу не менялось в интерфейсе.
    updated_by_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id"), nullable=True
    )
    # РЕШЕНИЕ (PR #183 review, п. 1): `onupdate` у колонки НЕТ, единственный механизм - явная
    # запись сервиса (`update_rules` ставит и `updated_at`, и `updated_by_account_id` вместе).
    # Причина: два механизма означали бы два возможных времени (часы БД и часы приложения), а
    # `onupdate` срабатывает и на ORM-обновлении, которое автора не ставит, - получилась бы
    # запись с новым временем и старым автором. Путь записи сейчас один; новый обязан ставить
    # обе колонки сам. Начальное значение - `server_default` (часы БД, миграция `0007`).
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
