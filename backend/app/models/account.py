from __future__ import annotations

import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

AUTH_TYPES = ("ldap", "local")
ROLES = ("employee", "hr_admin", "accountant", "system_admin")


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("auth_type IN ('ldap', 'local')", name="ck_accounts_auth_type"),
        CheckConstraint(
            "role IN ('employee', 'hr_admin', 'accountant', 'system_admin')",
            name="ck_accounts_role",
        ),
        # Ровно одно из двух: учётная запись привязана к карточке сотрудника,
        # либо это служебная запись (например, bootstrap-администратор,
        # FR-AUTH-005) со своим логином и без карточки.
        CheckConstraint(
            "num_nonnulls(employee_id, login) = 1",
            name="ck_accounts_employee_or_login",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), unique=True, nullable=True
    )
    login: Mapped[str | None] = mapped_column(unique=True, nullable=True)
    auth_type: Mapped[str] = mapped_column(nullable=False)
    local_password_hash: Mapped[str | None] = mapped_column(nullable=True)
    must_change_password: Mapped[bool] = mapped_column(nullable=False, default=False)
    failed_login_attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    locked_until: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    role: Mapped[str] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
