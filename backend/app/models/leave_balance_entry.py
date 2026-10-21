from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Тип события -> задача, которая его пишет (типов «на будущее» без задачи-
# писателя нет). Перечисление — VARCHAR + CheckConstraint, как в
# `LeaveType`.
ACCRUAL = "accrual"  # T-M2-05: начисление за рабочий год (FR-BAL-001)
USAGE = "usage"  # T-M3-34: списание дней при заявке
USAGE_REVERSAL = "usage_reversal"  # T-M3-34: возврат дней при отмене/отзыве (компенсирующее)
MANUAL_ADJUSTMENT = "manual_adjustment"  # T-M2-08 (FR-BAL-004)
RECALCULATION = "recalculation"  # T-M2-07: пересчёт при правке кадровых данных (FR-BAL-003)
INITIAL_IMPORT = "initial_import"  # T-M1-36: начальный остаток при импорте (FR-EMP-003)
TERMINATION_CLOSEOUT = "termination_closeout"  # T-M1-37: фиксация при увольнении (FR-EMP-004)

LEAVE_BALANCE_ENTRY_TYPES = (
    ACCRUAL,
    USAGE,
    USAGE_REVERSAL,
    MANUAL_ADJUSTMENT,
    RECALCULATION,
    INITIAL_IMPORT,
    TERMINATION_CLOSEOUT,
)

# Соглашение о знаке: знак несёт `quantity`, остаток — чистая SUM. Закреплено
# CHECK по типу события (иначе ошибка знака — тихая ошибка остатка).
POSITIVE_ENTRY_TYPES = (ACCRUAL, USAGE_REVERSAL, INITIAL_IMPORT)
NEGATIVE_ENTRY_TYPES = (USAGE,)
NONZERO_ENTRY_TYPES = (MANUAL_ADJUSTMENT, RECALCULATION, TERMINATION_CLOSEOUT)
# FR-BAL-003/004: причина именно у этих событий.
REASON_REQUIRED_ENTRY_TYPES = (MANUAL_ADJUSTMENT, RECALCULATION)
# События с автором-человеком; у остальных (начисление, списание по заявке)
# автора нет — их пишет система (начисление, списание и возврат по заявке).
ACTOR_REQUIRED_ENTRY_TYPES = (
    MANUAL_ADJUSTMENT,
    RECALCULATION,
    INITIAL_IMPORT,
    TERMINATION_CLOSEOUT,
)


def _type_list(values: tuple[str, ...]) -> str:
    return ", ".join(repr(value) for value in values)


class LeaveBalanceEntry(Base):
    """Событие по остатку отпуска (T-M2-02, `FR-BAL-001…004`).

    Остаток сотрудника по виду за рабочий год — СУММА `quantity` его
    событий; агрегирующей таблицы нет (`plan.md` §4). Журнал ДОБАВЛЯЕМЫЙ:
    исправление — новое компенсирующее событие, строки не правятся и не
    удаляются (сервис этих путей не содержит — проверяет тест; запрет на
    уровне прав БД — `NFR-AUDIT-001`, M6).

    Ссылки на заявку нет: `leave_requests` появится в M3, связь события
    `usage` с заявкой добавит миграция задачи `T-M3-34`.
    """

    __tablename__ = "leave_balance_entries"
    __table_args__ = (
        CheckConstraint(
            f"entry_type IN ({_type_list(LEAVE_BALANCE_ENTRY_TYPES)})",
            name="ck_leave_balance_entries_entry_type",
        ),
        CheckConstraint(
            "working_year_end > working_year_start",
            name="ck_leave_balance_entries_working_year",
        ),
        CheckConstraint(
            f"entry_type NOT IN ({_type_list(POSITIVE_ENTRY_TYPES)}) OR quantity > 0",
            name="ck_leave_balance_entries_positive_types",
        ),
        CheckConstraint(
            f"entry_type NOT IN ({_type_list(NEGATIVE_ENTRY_TYPES)}) OR quantity < 0",
            name="ck_leave_balance_entries_negative_types",
        ),
        CheckConstraint(
            f"entry_type NOT IN ({_type_list(NONZERO_ENTRY_TYPES)}) OR quantity <> 0",
            name="ck_leave_balance_entries_nonzero_types",
        ),
        CheckConstraint(
            f"entry_type NOT IN ({_type_list(REASON_REQUIRED_ENTRY_TYPES)}) "
            "OR reason IS NOT NULL",
            name="ck_leave_balance_entries_reason_required",
        ),
        # Пустая строка и пробелы — не причина. `\\S` в регулярных
        # выражениях PostgreSQL — не-пробельный символ; какие символы база
        # считает пробельными, зависит от её локали, поэтому вторая линия
        # защиты — обрезка `str.strip()` в сервисе, а не только эта проверка.
        CheckConstraint(
            "reason IS NULL OR reason ~ '\\S'",
            name="ck_leave_balance_entries_reason_not_blank",
        ),
        CheckConstraint(
            f"entry_type NOT IN ({_type_list(ACTOR_REQUIRED_ENTRY_TYPES)}) "
            "OR actor_account_id IS NOT NULL",
            name="ck_leave_balance_entries_actor_required",
        ),
        # Чтение остатка — `NFR-PERF-001`.
        Index(
            "ix_leave_balance_entries_balance",
            "employee_id",
            "leave_type_id",
            "working_year_start",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), nullable=False)
    leave_type_id: Mapped[int] = mapped_column(ForeignKey("leave_types.id"), nullable=False)
    # Рабочий год, к которому отнесено событие: `FR-BAL-001` считает
    # накопление в его пределах.
    working_year_start: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    working_year_end: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    entry_type: Mapped[str] = mapped_column(nullable=False)
    # Со знаком; единица (дни/часы) — по `leave_types.unit`.
    quantity: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    # Дата, на которую событие действует (не момент записи).
    entry_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    reason: Mapped[str | None] = mapped_column(nullable=True)
    actor_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
