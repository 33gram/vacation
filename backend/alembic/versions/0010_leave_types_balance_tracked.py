"""add leave_types.balance_tracked: the type keeps a balance (T-M2-20)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-26

ЧТО ДОБАВЛЯЕТ. Колонку `balance_tracked` (раздел 6 требований v1.26,
раздел 13 п. 32): ведёт ли Система по виду остаток - начисление,
списание, историю и показ в «Моём отпуске». Не то же, что
`reduces_main_balance` (использование вида списывает ОСНОВНОЙ остаток),
`annual_limit` (размер) и `is_paid` (оплата).

`server_default` ОСТАВЛЕН (ложь) - решение, а не недосмотр:
  - таблица заполнена (0006), колонка `NOT NULL` добавляется к существующим
    строкам, поэтому значение нужно уже на момент `ADD COLUMN`;
  - `seed_preset` из `0006` вызывают повторно (тесты; он пополнит набор
    будущей миграцией) и его INSERT не знает новой колонки: без значения
    по умолчанию на уровне БД `NOT NULL` ронял бы его даже при
    `ON CONFLICT DO NOTHING`;
  - вид, созданный HR через `POST /api/leave-types` (`T-M2-10`), без явного
    признака получает ложь и на уровне БД: остаток по новому виду не
    ведётся, пока HR не включит его осознанно.
Цена: БД молча принимает вставку без признака - явность требования
обеспечивает схема API (поле обязательно), а не колонка.

ЗНАЧЕНИЯ ВОСЬМИ ВИДОВ (раздел 13 п. 32). Три точных UPDATE по КОДУ, а не
по «всем строкам»: виды, заведённые администратором, остаются с ложью.
  - ДА `annual_paid`: `FR-BAL-001` начисляет по нему норму (BR-1).
  - ДА `additional_paid`: удлинённый и дополнительный отпуск - такое же
    накапливаемое право; `A-3.1` выводит из MVP сотрудников таких
    категорий, а не сам атрибут.
  - ДА `time_off`: `FR-DAYOFF-001` требует вести отдельный счётчик
    накопленной компенсации. Единица вида пока `working_days`, не часы
    (разрыв - `T-M3-31`); признак от единицы не зависит.
  - НЕТ (остаются с ложью) `unpaid` (права не накапливает, влияет на
    остаток только через стаж, ст. 121 ТК РФ), `study` (по документу,
    годового пула нет), `maternity_childcare` (длительность по закону и
    документу), `sick` (по листку, BR-10), `business_trip` (раздел 6:
    не расходует остаток отпуска).
Миграция не импортирует код приложения (замороженный снимок схемы).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRACKED_CODES = ("annual_paid", "additional_paid", "time_off")

_LEAVE_TYPES = sa.table(
    "leave_types",
    sa.column("code", sa.String),
    sa.column("balance_tracked", sa.Boolean),
)


def upgrade() -> None:
    op.add_column(
        "leave_types",
        sa.Column(
            "balance_tracked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    for code in _TRACKED_CODES:
        op.execute(
            sa.update(_LEAVE_TYPES)
            .where(_LEAVE_TYPES.c.code == code)
            .values(balance_tracked=True)
        )


def downgrade() -> None:
    op.drop_column("leave_types", "balance_tracked")
