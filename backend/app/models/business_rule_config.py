from __future__ import annotations

import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# `integer` — десятичное целое без знака; `month_day` — дата без года `ММ-ДД`.
# Новый тип значения — единственный случай, требующий миграции (CHECK ниже).
BUSINESS_RULE_VALUE_TYPES = ("integer", "month_day")


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
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
