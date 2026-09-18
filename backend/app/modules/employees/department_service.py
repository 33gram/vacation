"""Сервис управления оргструктурой (T-M1-10/T-M1-14, FR-EMP-002).

`departments` — дерево произвольной вложенности через self-FK
`parent_id`. Подразделение создаётся без руководителя
(`head_employee_id = NULL`) — иначе Систему нельзя развернуть с нуля,
пока не заведена ни одна карточка сотрудника; руководитель назначается
отдельным `PUT`.

Решение (см. тело коммита T-M1-10/T-M1-14): когда сотрудник, назначенный
руководителем подразделения, переводится в другое подразделение,
`head_employee_id` НЕ очищается автоматически — назначение и снятие
руководителя подразделения остаётся действием, которое выполняется
только через `PUT /api/departments/{id}`, а не побочным эффектом правки
карточки сотрудника в другом модуле. Это сознательно: если бы перевод
сотрудника менял состояние `departments`, это же поле мутировалось бы из
двух разных мест кода, что не позволяет предсказать, какой код в итоге
владеет значением.
"""

from __future__ import annotations

from sqlalchemy import literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.department import Department
from app.models.employee import Employee
from app.schemas.department import DepartmentCreate, DepartmentUpdate

# Тот же принцип, что и у `MAX_MANAGEMENT_CHAIN_DEPTH`
# (`app/modules/auth/rbac.py`): запрет циклов при записи — первая линия
# защиты, а не единственная, а обход дерева — один рекурсивный запрос,
# а не цикл с N обращениями к БД.
MAX_DEPARTMENT_TREE_DEPTH = 1000


class DepartmentNotFoundError(Exception):
    """Подразделение, которое правят (`PUT /api/departments/{id}`),
    отсутствует — 404, в отличие от некорректной ссылки в теле запроса
    (несуществующий `parent_id`/`head_employee_id`, цикл), которая
    остаётся `ValueError`/400: тело запроса синтаксически корректно, но
    ссылается не туда, а не адресует несуществующий ресурс."""


async def list_departments(session: AsyncSession) -> list[Department]:
    result = await session.execute(select(Department))
    return list(result.scalars().all())


async def _require_department(session: AsyncSession, department_id: int) -> Department:
    department = await session.get(Department, department_id)
    if department is None:
        raise DepartmentNotFoundError(f"Department {department_id} not found")
    return department


async def _require_parent_reference(session: AsyncSession, parent_id: int) -> None:
    if await session.get(Department, parent_id) is None:
        raise ValueError(f"Parent department {parent_id} not found")


async def _would_create_cycle(
    session: AsyncSession, department_id: int, new_parent_id: int
) -> bool:
    """Стало бы `new_parent_id` потомком `department_id`, если сделать
    его родителем `department_id`? Проверяем, не является ли
    `department_id` уже (транзитивным) предком `new_parent_id`."""
    if department_id == new_parent_id:
        return True

    ancestors = (
        select(Department.parent_id.label("current_parent_id"), literal(1).label("depth"))
        .where(Department.id == new_parent_id)
        .cte("department_ancestors", recursive=True)
    )
    parent = aliased(Department)
    ancestors = ancestors.union_all(
        select(parent.parent_id, ancestors.c.depth + 1)
        .select_from(ancestors)
        .join(parent, parent.id == ancestors.c.current_parent_id)
        .where(ancestors.c.depth < MAX_DEPARTMENT_TREE_DEPTH)
    )
    result = await session.execute(
        select(literal(1))
        .select_from(ancestors)
        .where(ancestors.c.current_parent_id == department_id)
        .limit(1)
    )
    return result.first() is not None


async def create_department(session: AsyncSession, payload: DepartmentCreate) -> Department:
    if payload.parent_id is not None:
        await _require_parent_reference(session, payload.parent_id)

    department = Department(name=payload.name, parent_id=payload.parent_id, head_employee_id=None)
    session.add(department)
    await session.commit()
    await session.refresh(department)
    return department


async def update_department(
    session: AsyncSession, department_id: int, payload: DepartmentUpdate
) -> Department:
    department = await _require_department(session, department_id)

    if payload.parent_id is not None:
        await _require_parent_reference(session, payload.parent_id)
        if await _would_create_cycle(session, department_id, payload.parent_id):
            raise ValueError("Department cannot become a descendant of its own descendant")

    if payload.head_employee_id is not None:
        head = await session.get(Employee, payload.head_employee_id)
        if head is None:
            raise ValueError(f"Employee {payload.head_employee_id} not found")

    department.name = payload.name
    department.parent_id = payload.parent_id
    department.head_employee_id = payload.head_employee_id

    await session.commit()
    await session.refresh(department)
    return department
