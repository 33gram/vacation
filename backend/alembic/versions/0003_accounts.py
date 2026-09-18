"""create accounts table (T-M1-03)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-18

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("employees.id"),
            unique=True,
            nullable=True,
        ),
        # Логин служебных записей без карточки сотрудника (bootstrap-
        # администратор, FR-AUTH-005). Для записей с employee_id логином
        # служит employees.email — здесь остаётся NULL.
        sa.Column("login", sa.String(), unique=True, nullable=True),
        sa.Column("auth_type", sa.String(), nullable=False),
        sa.Column("local_password_hash", sa.String(), nullable=True),
        sa.Column(
            "must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "failed_login_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint("auth_type IN ('ldap', 'local')", name="ck_accounts_auth_type"),
        sa.CheckConstraint(
            "role IN ('employee', 'hr_admin', 'accountant', 'system_admin')",
            name="ck_accounts_role",
        ),
        sa.CheckConstraint(
            "num_nonnulls(employee_id, login) = 1",
            name="ck_accounts_employee_or_login",
        ),
    )


def downgrade() -> None:
    op.drop_table("accounts")
