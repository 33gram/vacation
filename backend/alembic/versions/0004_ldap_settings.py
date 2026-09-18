"""create ldap_settings table (T-M1-04)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-18

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ldap_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("server_url", sa.String(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="389"),
        sa.Column("base_dn", sa.String(), nullable=False),
        sa.Column("bind_dn", sa.String(), nullable=False),
        # Обратимое шифрование на уровне приложения (app.core.crypto), ключ —
        # LDAP_PASSWORD_ENCRYPTION_KEY (T-000).
        sa.Column("bind_password_encrypted", sa.String(), nullable=False),
        sa.Column(
            "user_match_attribute", sa.String(), nullable=False, server_default="mail"
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_status", sa.String(), nullable=True),
        sa.CheckConstraint(
            "last_test_status IS NULL OR last_test_status IN ('success', 'failure')",
            name="ck_ldap_settings_last_test_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("ldap_settings")
