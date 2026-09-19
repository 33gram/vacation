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


class EmployeeCardFields(BaseModel):
    """Девять полей карточки, общих для создания и правки (PR review:
    раньше этот список был продублирован в `EmployeeCreate`,
    `EmployeeUpdate`, конструкторе `create_employee` и блоке
    присваиваний `update_employee` — теперь единственный канонический
    источник; `employee_service._CARD_FIELD_NAMES` берёт имена полей
    отсюда же, а не хранит собственный список)."""

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


class EmployeeCreate(EmployeeCardFields):
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


class EmployeeDeactivate(BaseModel):
    """`POST /api/employees/{id}/deactivate` (`T-M1-12`, `FR-EMP-004`,
    без части про фиксацию остатка — см. `T-M1-37`)."""

    termination_date: datetime.date


class EmployeeUpdate(EmployeeCardFields):
    """Правка карточки, включая способ входа (`T-M1-34`). Статус
    занятости (`employment_status`) сюда не входит — меняется только
    деактивацией (`T-M1-12`, ещё не реализована).

    `auth_type` — обязательное поле, как и остальные (PUT — полная
    замена, а не частичный PATCH): клиент всегда явно подтверждает
    текущий способ входа, даже если не меняет его. Обязательность
    `new_password` при переходе на `local` схема проверить не может —
    это зависит от ТЕКУЩЕГО способа входа учётной записи, а не только от
    тела запроса, — эту часть проверяет сервис."""

    auth_type: EmployeeAuthType
    # SecretStr — см. `EmployeeCreate.initial_password`; принимается
    # только при переходе с `ldap` на `local` (проверяет сервис).
    new_password: SecretStr | None = None

    @model_validator(mode="after")
    def _validate_new_password(self) -> EmployeeUpdate:
        if self.auth_type == "ldap" and self.new_password is not None:
            raise ValueError("new_password must not be set when auth_type is 'ldap'")
        return self
