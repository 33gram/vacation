"""Pydantic-схемы для `/api/departments*` (T-M1-14, FR-EMP-002)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    parent_id: int | None
    head_employee_id: int | None


class DepartmentCreate(BaseModel):
    name: str
    parent_id: int | None = None
    # `head_employee_id` намеренно отсутствует: подразделение всегда
    # создаётся без руководителя — иначе Систему нельзя развернуть с
    # нуля (назначить некого, пока не создана ни одна карточка).
    # Руководитель назначается отдельным `PUT /api/departments/{id}`.


class DepartmentUpdate(BaseModel):
    name: str
    parent_id: int | None = None
    head_employee_id: int | None = None
