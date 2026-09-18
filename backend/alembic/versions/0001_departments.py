"""create departments table (T-M1-01)

Revision ID: 0001
Revises:
Create Date: 2026-09-18

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "parent_id",
            sa.Integer(),
            sa.ForeignKey("departments.id"),
            nullable=True,
        ),
        # FK на employees.id добавляется в 0002 (миграция T-M1-02) — employees
        # ещё не существует на этом шаге (циклическая связь департамент<->руководитель).
        sa.Column("head_employee_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("departments")
