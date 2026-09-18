from __future__ import annotations

import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

WORK_SCHEDULE_TYPES = ("standard", "shift")
EMPLOYMENT_STATUSES = ("active", "terminated")


class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (
        CheckConstraint(
            "work_schedule_type IN ('standard', 'shift')",
            name="ck_employees_work_schedule_type",
        ),
        CheckConstraint(
            "employment_status IN ('active', 'terminated')",
            name="ck_employees_employment_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(nullable=False)
    personnel_number: Mapped[str] = mapped_column(unique=True, nullable=False)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    position: Mapped[str] = mapped_column(nullable=False)
    hire_date: Mapped[datetime.date] = mapped_column(nullable=False)
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    email: Mapped[str] = mapped_column(unique=True, nullable=False)
    work_schedule_type: Mapped[str] = mapped_column(nullable=False, default="standard")
    employment_status: Mapped[str] = mapped_column(nullable=False, default="active")
    termination_date: Mapped[datetime.date | None] = mapped_column(nullable=True)
    # Зарезервировано под A-3.1 (льготные категории отпуска) — не заполняется в MVP.
    leave_category: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
