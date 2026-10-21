"""create leave_balance_entries, the leave balance event journal (T-M2-02)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-21

ЧТО ЭТО. Не таблица «баланс сотрудника», а ЖУРНАЛ СОБЫТИЙ: строка — одно
событие по остатку, остаток — сумма `quantity` событий сотрудника по виду
за рабочий год (`plan.md` §4, решение владельца продукта). Агрегирующей
строки нет. Вычисляет остаток одна функция сервиса `app/modules/balances`
(`T-M2-08`), а не представление БД — второе место с логикой остатка
пришлось бы поддерживать в миграциях.

ЗАЧЕМ ЖУРНАЛ. `FR-BAL-002` обещает сотруднику историю начислений и
использования, `FR-BAL-003` — запись с автором и причиной, `FR-BAL-004` —
причину у КАЖДОЙ корректировки: при колонке `adjustment_reason` в строке-
агрегате вторая корректировка затёрла бы причину первой.

СОГЛАШЕНИЕ О ЗНАКЕ — ВЫБРАНО: знак несёт `quantity`, поэтому остаток —
чистая `SUM` без ветвления по типу. Закреплено `CHECK` по типу события,
иначе ошибка знака стала бы тихой арифметической ошибкой остатка:
  - `accrual`, `usage_reversal`, `initial_import` > 0;
  - `usage` < 0;
  - `manual_adjustment`, `recalculation`, `termination_closeout` <> 0.
Начальный импорт строго положителен — ВЫБРАНО: остаток «авансом»
(`FR-BAL-001`, стаж < 6 месяцев) при переносе исправляется ручной
корректировкой с причиной, а не отрицательной строкой импорта: опечатка
в знаке при массовой загрузке иначе прошла бы молча.

ТИПЫ СОБЫТИЙ — только те, у которых есть задача-писатель:
  - `accrual` — `T-M2-05` (начисление, `FR-BAL-001`);
  - `usage` — `T-M3-34` (списание дней при заявке);
  - `usage_reversal` — `T-M3-34` (возврат дней при отмене/отзыве заявки):
    журнал добавляемый, поэтому возврат — отдельное положительное событие,
    а не правка списания; тип отдельный, чтобы знак `usage` оставался
    защищён (usage < 0) и возврат не маскировался под ручную корректировку,
    которая требует автора-человека и причину;
  - `manual_adjustment` — `T-M2-08` (`FR-BAL-004`);
  - `recalculation` — `T-M2-07` (`FR-BAL-003`): разность, а не новое
    итоговое значение;
  - `initial_import` — `T-M1-36` (`FR-EMP-003`);
  - `termination_closeout` — `T-M1-37` (`FR-EMP-004`): событие, которое
    приводит остаток на дату увольнения к нулю (`quantity` = минус
    остаток), так что фиксируемая величина — сам модуль события.
    ВЫБРАНО здесь; `T-M1-37` вправе пересмотреть это миграцией.

ПРИЧИНА И АВТОР выражены `CHECK`, а не соглашением в коде: причина
обязательна у `manual_adjustment` и `recalculation` (`FR-BAL-003/004`) и
не может быть пустой нигде; автор (`actor_account_id`) обязателен у
событий, которые делает человек (корректировка, перерасчёт, импорт,
фиксация при увольнении), и пуст у системных (начисление, списание и возврат
по заявке). Импорт и фиксация при увольнении отнесены к «человеческим» —
ВЫБРАНО: их запускает HR-администратор, и без автора по журналу не
понять, кто внёс начальные остатки.

СВЯЗИ С ЗАЯВКОЙ НЕТ: `leave_requests` появится в M3, колонка без
референта — значение без читателя. Связь добавит миграция `T-M3-34`.

ДОБАВЛЯЕМЫЙ. Исправление — новое событие, не правка строки; путей
`UPDATE`/`DELETE` в сервисе нет (проверяет тест). Запрет на уровне прав БД
для роли приложения (как у `audit_log`) — `NFR-AUDIT-001`, этап M6; здесь
его нет, и до M6 защита только прикладная.

`downgrade` вместе с таблицей теряет все события, то есть весь остаток:
при откате в разработке это допустимо, на рабочей базе `downgrade` этой
миграции выполнять нельзя.

Наборы типов в `CHECK` записаны литералами, а не берутся из
`app.models.leave_balance_entry`: миграция — замороженный снимок. Их
расхождение с моделью само не ловится (`T-M1-62`).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "leave_balance_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("leave_type_id", sa.Integer(), sa.ForeignKey("leave_types.id"), nullable=False),
        sa.Column("working_year_start", sa.Date(), nullable=False),
        sa.Column("working_year_end", sa.Date(), nullable=False),
        sa.Column("entry_type", sa.String(), nullable=False),
        sa.Column("quantity", sa.Numeric(7, 2), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("actor_account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "entry_type IN ('accrual', 'usage', 'usage_reversal', 'manual_adjustment', "
            "'recalculation', 'initial_import', 'termination_closeout')",
            name="ck_leave_balance_entries_entry_type",
        ),
        sa.CheckConstraint(
            "working_year_end > working_year_start",
            name="ck_leave_balance_entries_working_year",
        ),
        sa.CheckConstraint(
            "entry_type NOT IN ('accrual', 'usage_reversal', 'initial_import') OR quantity > 0",
            name="ck_leave_balance_entries_positive_types",
        ),
        sa.CheckConstraint(
            "entry_type NOT IN ('usage') OR quantity < 0",
            name="ck_leave_balance_entries_negative_types",
        ),
        sa.CheckConstraint(
            "entry_type NOT IN ('manual_adjustment', 'recalculation', 'termination_closeout') "
            "OR quantity <> 0",
            name="ck_leave_balance_entries_nonzero_types",
        ),
        sa.CheckConstraint(
            "entry_type NOT IN ('manual_adjustment', 'recalculation') OR reason IS NOT NULL",
            name="ck_leave_balance_entries_reason_required",
        ),
        sa.CheckConstraint(
            "reason IS NULL OR reason ~ '\\S'",
            name="ck_leave_balance_entries_reason_not_blank",
        ),
        sa.CheckConstraint(
            "entry_type NOT IN ('manual_adjustment', 'recalculation', 'initial_import', "
            "'termination_closeout') OR actor_account_id IS NOT NULL",
            name="ck_leave_balance_entries_actor_required",
        ),
    )
    op.create_index(
        "ix_leave_balance_entries_balance",
        "leave_balance_entries",
        ["employee_id", "leave_type_id", "working_year_start"],
    )


def downgrade() -> None:
    op.drop_index("ix_leave_balance_entries_balance", table_name="leave_balance_entries")
    op.drop_table("leave_balance_entries")
