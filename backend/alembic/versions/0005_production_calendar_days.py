"""create production_calendar_days table (T-M1-05)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-18

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "production_calendar_days",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.Date(), nullable=False, unique=True),
        sa.Column("day_type", sa.String(), nullable=False),
        sa.CheckConstraint(
            "day_type IN ('working', 'weekend', 'holiday')",
            name="ck_production_calendar_days_day_type",
        ),
    )


def downgrade() -> None:
    op.drop_table("production_calendar_days")
