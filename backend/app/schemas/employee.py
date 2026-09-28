"""Pydantic-схемы для `/api/employees*` (T-M1-15, FR-EMP-001 в редакции v1.7)."""

from __future__ import annotations

import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.modules.auth.password_policy import MAX_PASSWORD_LENGTH_CHARS

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
    # T-M1-52: живёт на связанной `accounts` строке, не на `employees` —
    # `EmployeeOut` больше не строится голым `model_validate(employee)`
    # (`from_attributes=True` не дотянулся бы до другой таблицы), значение
    # собирает `employee_service.to_employee_out`/`to_employee_out_list`.
    # Без этого поля `PUT /api/employees/{id}` (обязательное на каждый
    # запрос, полная замена) заставлял клиента угадывать текущий способ
    # входа — угадав неверно, правка любого постороннего поля молча
    # переводила бы сотрудника на другой способ входа.
    auth_type: EmployeeAuthType


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
    # max_length — PR #140 review, см. `LoginRequest.password`.
    initial_password: SecretStr | None = Field(default=None, max_length=MAX_PASSWORD_LENGTH_CHARS)

    @model_validator(mode="after")
    def _validate_initial_password(self) -> EmployeeCreate:
        has_password = (
            self.initial_password is not None
            and self.initial_password.get_secret_value().strip()
        )
        if self.auth_type == "local" and not has_password:
            raise ValueError("initial_password обязателен, когда auth_type = 'local'")
        if self.auth_type == "ldap" and self.initial_password is not None:
            raise ValueError("initial_password не должен быть задан, когда auth_type = 'ldap'")
        return self


class EmployeeDeactivate(BaseModel):
    """`POST /api/employees/{id}/deactivate` (`T-M1-12`, `FR-EMP-004`,
    без части про фиксацию остатка — см. `T-M1-37`)."""

    termination_date: datetime.date


class ImportRowError(BaseModel):
    """`POST /api/employees/import` (`T-M1-11`, `FR-EMP-003`). Отчёт
    возвращается загрузившему его HR-администратору, которому эти
    данные и так доступны — значения полей (`reason` может содержать
    само значение ячейки) в ответе допустимы; в логи они не попадают
    (см. `employee_router.py`)."""

    row_number: int
    field: str
    reason: str


class ImportSummary(BaseModel):
    accepted_count: int
    rejected_count: int
    errors: list[ImportRowError]


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
    new_password: SecretStr | None = Field(default=None, max_length=MAX_PASSWORD_LENGTH_CHARS)

    @model_validator(mode="after")
    def _validate_new_password(self) -> EmployeeUpdate:
        if self.auth_type == "ldap" and self.new_password is not None:
            raise ValueError("new_password не должен быть задан, когда auth_type = 'ldap'")
        return self
