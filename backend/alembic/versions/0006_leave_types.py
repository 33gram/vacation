"""create leave_types table (T-M2-01)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-19

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.create_table(
        "leave_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False, unique=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("is_paid", sa.String(), nullable=False),
        sa.Column("unit", sa.String(), nullable=False),
        sa.Column("reduces_main_balance", sa.Boolean(), nullable=False),
        sa.Column("requires_document", sa.Boolean(), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("annual_limit", sa.Numeric(7, 2), nullable=True),
        # Набор значений сознательно минимальный (решение владельца
        # продукта): поведение переноса не определено ни одним FR и никем не
        # читается (T-M3-30). Случай «не более N дней» потребует ОТДЕЛЬНОЙ
        # колонки с лимитом переноса и миграции, а не нового значения здесь.
        sa.Column("carry_over_policy", sa.String(), nullable=False),
        sa.Column("exclude_weekends_holidays", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "is_paid IN ('full', 'none', 'partial')",
            name="ck_leave_types_is_paid",
        ),
        sa.CheckConstraint(
            "unit IN ('calendar_days', 'working_days', 'hours')",
            name="ck_leave_types_unit",
        ),
        sa.CheckConstraint(
            "carry_over_policy IN ('none', 'unlimited')",
            name="ck_leave_types_carry_over_policy",
        ),
        sa.CheckConstraint(
            "annual_limit IS NULL OR annual_limit >= 0",
            name="ck_leave_types_annual_limit_non_negative",
        ),
    )


def downgrade() -> None:
    op.drop_table("leave_types")
