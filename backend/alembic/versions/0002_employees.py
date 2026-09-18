"""create employees table (T-M1-02)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-18

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("personnel_number", sa.String(), nullable=False, unique=True),
        sa.Column(
            "department_id",
            sa.Integer(),
            sa.ForeignKey("departments.id"),
            nullable=True,
        ),
        sa.Column("position", sa.String(), nullable=False),
        sa.Column("hire_date", sa.Date(), nullable=False),
        sa.Column(
            "manager_id",
            sa.Integer(),
            sa.ForeignKey("employees.id"),
            nullable=True,
        ),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column(
            "work_schedule_type", sa.String(), nullable=False, server_default="standard"
        ),
        sa.Column(
            "employment_status", sa.String(), nullable=False, server_default="active"
        ),
        sa.Column("termination_date", sa.Date(), nullable=True),
        sa.Column("leave_category", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "work_schedule_type IN ('standard', 'shift')",
            name="ck_employees_work_schedule_type",
        ),
        sa.CheckConstraint(
            "employment_status IN ('active', 'terminated')",
            name="ck_employees_employment_status",
        ),
    )

    # Циклическая связь departments.head_employee_id -> employees.id,
    # отложенная из 0001 до появления таблицы employees.
    op.create_foreign_key(
        "fk_departments_head_employee_id",
        "departments",
        "employees",
        ["head_employee_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_departments_head_employee_id", "departments", type_="foreignkey"
    )
    op.drop_table("employees")
