from __future__ import annotations

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    # Циклическая связь с employees (employees.department_id -> departments.id):
    # use_alter откладывает создание этого FK до момента, когда таблица
    # employees уже существует (см. миграцию T-M1-02).
    head_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", use_alter=True, name="fk_departments_head_employee_id"),
        nullable=True,
    )
