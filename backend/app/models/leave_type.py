from __future__ import annotations

from decimal import Decimal

from sqlalchemy import CheckConstraint, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Перечисления — VARCHAR + CheckConstraint, а не нативные типы PostgreSQL:
# такова форма, принятая в репозитории (`LdapSettings.last_test_status`,
# `ProductionCalendarDay.day_type`).
LEAVE_TYPE_PAID_VALUES = ("full", "none", "partial")
LEAVE_TYPE_UNIT_VALUES = ("calendar_days", "working_days", "hours")
LEAVE_TYPE_CARRY_OVER_VALUES = ("none", "unlimited")
LEAVE_TYPE_COUNTED_NON_WORKING_DAYS_VALUES = ("all", "weekends_only", "none")


def _in_set(column: str, values: tuple[str, ...]) -> str:
    """`column IN ('a', 'b')` из набора значений — одна копия набора в
    приложении. Миграция `0006` повторяет их литералами намеренно (она
    замороженный снимок схемы, см. комментарий там)."""
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class LeaveType(Base):
    """Справочник видов отсутствий (T-M2-01, раздел 6 требований,
    `FR-ADMIN-001`).

    Ни у одной колонки нет значения по умолчанию: раздел 6 определяет
    набор атрибутов, но не их умолчания, а выдумывать умолчание в схеме
    значило бы молча принять за требование то, чего в требованиях нет.
    """

    __tablename__ = "leave_types"
    __table_args__ = (
        CheckConstraint(
            _in_set("is_paid", LEAVE_TYPE_PAID_VALUES),
            name="ck_leave_types_is_paid",
        ),
        CheckConstraint(
            _in_set("unit", LEAVE_TYPE_UNIT_VALUES),
            name="ck_leave_types_unit",
        ),
        CheckConstraint(
            _in_set("carry_over_policy", LEAVE_TYPE_CARRY_OVER_VALUES),
            name="ck_leave_types_carry_over_policy",
        ),
        CheckConstraint(
            _in_set(
                "counted_non_working_days", LEAVE_TYPE_COUNTED_NON_WORKING_DAYS_VALUES
            ),
            name="ck_leave_types_counted_non_working_days",
        ),
        CheckConstraint(
            "annual_limit IS NULL OR annual_limit >= 0",
            name="ck_leave_types_annual_limit_non_negative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Стабильный идентификатор вида: на него ссылаются `leave_requests`,
    # тесты и фронтенд, поэтому после создания не меняется (меняется `name`).
    code: Mapped[str] = mapped_column(unique=True, nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    is_paid: Mapped[str] = mapped_column(nullable=False)
    # В единицах `unit` измеряется и `annual_limit`, и (в `leave_requests`)
    # количество.
    unit: Mapped[str] = mapped_column(nullable=False)
    reduces_main_balance: Mapped[bool] = mapped_column(nullable=False)
    requires_document: Mapped[bool] = mapped_column(nullable=False)
    # Только факт «нужно ли согласование»; МАРШРУТ (раздел 6: «и по какому
    # маршруту») живёт не здесь, а в `approval_routes.leave_type_id`
    # (`plan.md` §4, `T-M3-04`).
    requires_approval: Mapped[bool] = mapped_column(nullable=False)
    # NULL — лимита нет. Нормы закона (28 дней основного отпуска, BR-1)
    # здесь не дублируются: они живут в `business_rules_config` (правило 5).
    annual_limit: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    # Набор значений СОЗНАТЕЛЬНО минимальный (решение владельца продукта).
    # Поведение переноса не определено ни одним FR, колонка хранится, но
    # никем не читается (`T-M3-30`). Случай «переносится не более N дней»
    # значением не выразить: ему нужна отдельная колонка с лимитом переноса
    # и миграция.
    carry_over_policy: Mapped[str] = mapped_column(nullable=False)
    # Какие нерабочие дни периода (по производственному календарю, BR-11)
    # входят в количество: `all` — все дни периода; `weekends_only` —
    # выходные входят, праздничные нет (ст. 120 ТК РФ, ежегодные отпуска);
    # `none` — только рабочие дни. Раздел 6 требований v1.21.
    counted_non_working_days: Mapped[str] = mapped_column(nullable=False)
    # Выключенный вид остаётся в справочнике (A-3.1); влияние на подачу
    # заявок ни одним FR не определено — решается в `FR-REQ-*`.
    is_active: Mapped[bool] = mapped_column(nullable=False)
