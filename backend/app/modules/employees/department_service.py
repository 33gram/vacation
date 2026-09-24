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

from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.department import Department
from app.models.employee import Employee
from app.modules.employees.employee_service import lock_management_chain
from app.schemas.department import DepartmentCreate, DepartmentUpdate

# Тот же принцип, что и у `MAX_MANAGEMENT_CHAIN_DEPTH`
# (`app/modules/auth/rbac.py`): запрет циклов при записи — первая линия
# защиты, а не единственная, а обход дерева — один рекурсивный запрос,
# а не цикл с N обращениями к БД.
MAX_DEPARTMENT_TREE_DEPTH = 1000

# Константа, никак не связанная с id конкретных подразделений — общий
# ключ на весь инвариант "дерево подразделений без циклов", а не на пару
# правящихся строк (см. `_lock_department_tree`). Число выбрано
# произвольно, лишь бы не совпадало с `MANAGEMENT_CHAIN_LOCK_KEY`
# (`employee_service.py`) — деревья независимы, один ключ на оба не
# нужен.
DEPARTMENT_TREE_LOCK_KEY = 7_270_010_101


async def _lock_department_tree(session: AsyncSession) -> None:
    """PR review: под READ COMMITTED (по умолчанию) две конкурентные
    транзакции, назначающие подразделения родителями друг друга, обе
    читают состояние ДО правки другой, обе не видят будущего цикла — и
    обе коммитят своё ребро (write skew). Повторная проверка цикла перед
    записью этого не ловит: она читает то же самое согласованное, но
    устаревшее состояние.

    `pg_advisory_xact_lock` сериализует конкурентные правки дерева между
    собой: снимается автоматически коммитом/откатом ТЕКУЩЕЙ транзакции,
    поэтому взявший блокировку вторым увидит уже закоммиченное ребро
    первого. Берётся здесь, ДО проверки цикла, — иначе окно гонки
    остаётся.

    Правки оргструктуры делает HR, они редки: сериализовать их между
    собой дешевле и проще, чем ловить конфликты `SERIALIZABLE` с retry-
    циклом на каждый запрос. Это НЕ защита на уровне БД — рекурсивный
    `CHECK`, запрещающий цикл, в PostgreSQL невыразим, — а защита именно
    конкурентных запросов через API; прямая правка таблицы в обход
    приложения по-прежнему может создать цикл (для данных, пришедших
    так, — вторая линия защиты, ограничение глубины обхода в
    `_would_create_cycle`)."""
    await session.execute(select(func.pg_advisory_xact_lock(DEPARTMENT_TREE_LOCK_KEY)))


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
        raise DepartmentNotFoundError(f"Подразделение {department_id} не найдено")
    return department


async def _require_parent_reference(session: AsyncSession, parent_id: int) -> None:
    if await session.get(Department, parent_id) is None:
        raise ValueError(f"Родительское подразделение {parent_id} не найдено")


async def _require_active_head_reference(session: AsyncSession, head_employee_id: int) -> None:
    """PR #139 review, замечание B: раньше проверялось только
    существование сотрудника — `PUT /api/departments/{id}` с id
    уволенного отвечал 200. Симметрично `employee_service._require_
    active_manager_reference` (назначение уволенного НЕПОСРЕДСТВЕННЫМ
    руководителем уже запрещено) — оставлять уволенного руководителем
    подразделения было второй, незакрытой половиной того же
    противоречия. Вызывающая сторона обязана взять `lock_management_
    chain` ДО этой проверки — иначе гонка с конкурентной деактивацией
    (проверка читает сотрудника активным, деактивация коммитит
    увольнение, обе стороны проходят)."""
    head = await session.get(Employee, head_employee_id)
    if head is None:
        raise ValueError(f"Сотрудник {head_employee_id} не найден")
    if head.employment_status != "active":
        raise ValueError(
            "head_employee_id: уволенный сотрудник не может быть руководителем подразделения"
        )


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
        await _lock_department_tree(session)
        await _require_parent_reference(session, payload.parent_id)
        if await _would_create_cycle(session, department_id, payload.parent_id):
            raise ValueError("Подразделение не может стать потомком собственного потомка")

    if payload.head_employee_id is not None:
        await lock_management_chain(session)
        await _require_active_head_reference(session, payload.head_employee_id)

    department.name = payload.name
    department.parent_id = payload.parent_id
    department.head_employee_id = payload.head_employee_id

    await session.commit()
    await session.refresh(department)
    return department
