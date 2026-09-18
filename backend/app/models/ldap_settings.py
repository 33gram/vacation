from __future__ import annotations

import datetime

from sqlalchemy import CheckConstraint, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

LDAP_TEST_STATUSES = ("success", "failure")


class LdapSettings(Base):
    __tablename__ = "ldap_settings"
    __table_args__ = (
        CheckConstraint(
            "last_test_status IS NULL OR last_test_status IN ('success', 'failure')",
            name="ck_ldap_settings_last_test_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    server_url: Mapped[str] = mapped_column(nullable=False)
    port: Mapped[int] = mapped_column(nullable=False, default=389)
    base_dn: Mapped[str] = mapped_column(nullable=False)
    bind_dn: Mapped[str] = mapped_column(nullable=False)
    # Обратимое шифрование на уровне приложения (app.core.crypto),
    # ключ — LDAP_PASSWORD_ENCRYPTION_KEY (T-M1-04, T-000).
    bind_password_encrypted: Mapped[str] = mapped_column(nullable=False)
    user_match_attribute: Mapped[str] = mapped_column(nullable=False, default="mail")
    is_active: Mapped[bool] = mapped_column(nullable=False, default=False)
    last_tested_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_test_status: Mapped[str | None] = mapped_column(nullable=True)
