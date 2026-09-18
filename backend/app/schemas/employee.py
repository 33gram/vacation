"""Pydantic-схемы для `/api/employees*` (T-M1-15, FR-EMP-001 в редакции v1.7)."""

from __future__ import annotations

import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator

WorkScheduleType = Literal["standard", "shift"]
EmployeeAuthType = Literal["ldap", "local"]


class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    personnel_number: str
    department_id: int | None
    position: str
    hire_date: datetime.date
    manager_id: int | None
    email: str
    work_schedule_type: str
    employment_status: str
    termination_date: datetime.date | None
    leave_category: str | None


class EmployeeCreate(BaseModel):
    full_name: str
    personnel_number: str
    department_id: int
    position: str
    hire_date: datetime.date
    # Пусто — только для вершины иерархии (FR-EMP-001 в редакции v1.7);
    # для всех остальных карточек обязателен и проверяется сервисом.
    manager_id: int | None = None
    email: str
    work_schedule_type: WorkScheduleType
    # Зарезервировано под A-3.1 — не заполняется в MVP.
    leave_category: str | None = None
    auth_type: EmployeeAuthType = "ldap"
    # SecretStr — маскирует repr()/str() (см. LdapTestRequest, PR #137);
    # обязателен при `auth_type = "local"`, запрещён при `"ldap"`.
    initial_password: SecretStr | None = None

    @model_validator(mode="after")
    def _validate_initial_password(self) -> EmployeeCreate:
        has_password = (
            self.initial_password is not None
            and self.initial_password.get_secret_value().strip()
        )
        if self.auth_type == "local" and not has_password:
            raise ValueError("initial_password is required when auth_type is 'local'")
        if self.auth_type == "ldap" and self.initial_password is not None:
            raise ValueError("initial_password must not be set when auth_type is 'ldap'")
        return self


class EmployeeUpdate(BaseModel):
    """Правка карточки: организационные и кадровые поля. Способ входа
    (`auth_type`) и статус занятости (`employment_status`) сюда
    намеренно не входят — первый меняется отдельным механизмом
    (`T-M1-34`), второй меняется только деактивацией (`T-M1-12`, ещё не
    реализована)."""

    full_name: str
    personnel_number: str
    department_id: int
    position: str
    hire_date: datetime.date
    manager_id: int | None = None
    email: str
    work_schedule_type: WorkScheduleType
    leave_category: str | None = None
