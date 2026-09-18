"""Сервис карточек сотрудников (T-M1-15, FR-EMP-001 в редакции v1.7).

Создание карточки и связанной учётной записи — одна транзакция (`plan.md`
§5.1): частичный результат оставил бы сотрудника, который не может
войти и которого нельзя дозавести повторным `POST` (табельный номер и
e-mail уже заняты первой, оборванной попыткой).

Права по таблице `plan.md` §5.1: ведение (`POST`/`PUT`) — `hr_admin`/
`system_admin`; просмотр своей карточки — любой аутентифицированный;
просмотр чужой — руководитель (своя команда, `is_in_management_chain`/
`list_subordinate_employee_ids` из `rbac.py`), `hr_admin`/`system_admin`
(все), `accountant` (все, только чтение).
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.account import Account
from app.models.department import Department
from app.models.employee import Employee
from app.modules.auth.password_policy import validate_password_strength
from app.modules.auth.rbac import is_in_management_chain, list_subordinate_employee_ids
from app.modules.auth.repository import get_account_by_employee_id
from app.schemas.employee import EmployeeCreate, EmployeeUpdate

# Роли, видящие ЛЮБУЮ карточку (`plan.md` §5.1); `accountant` — только
# чтение, ограничение на запись обеспечивает `require_roles` в роутере,
# а не эта проверка.
PRIVILEGED_VIEW_ROLES = frozenset({"hr_admin", "system_admin", "accountant"})


class EmployeeNotFoundError(Exception):
    """Карточка, которую правят (`PUT /api/employees/{id}`), отсутствует
    — 404, в отличие от некорректной ссылки в теле запроса (несуществующий
    `department_id`/`manager_id`, дубликат), которая остаётся
    `ValueError`/400 (см. `department_service.DepartmentNotFoundError`,
    тот же принцип)."""


async def get_employee(session: AsyncSession, employee_id: int) -> Employee | None:
    return await session.get(Employee, employee_id)


async def can_view_employee(
    session: AsyncSession, account: Account, target_employee_id: int
) -> bool:
    if account.role in PRIVILEGED_VIEW_ROLES:
        return True
    if account.employee_id == target_employee_id:
        return True
    if account.employee_id is None:
        return False
    return await is_in_management_chain(session, account.employee_id, target_employee_id)


async def list_visible_employees(
    session: AsyncSession, account: Account, *, include_terminated: bool
) -> list[Employee]:
    """Штат до 750 человек (`NFR-SCALE-001`) — решение: без постраничной
    выдачи, весь отфильтрованный список одним ответом. 750 компактных
    JSON-объектов — не тот объём, ради которого стоит вводить курсор/
    offset-пагинацию на MVP; вводить её позже, если штат вырастет
    настолько, что понадобится, — обратно совместимая правка (появление
    query-параметров `limit`/`offset` не ломает текущих клиентов)."""
    if account.role not in PRIVILEGED_VIEW_ROLES:
        if account.employee_id is None:
            return []
        subordinate_ids = await list_subordinate_employee_ids(session, account.employee_id)
        visible_ids = {account.employee_id, *subordinate_ids}
        query = select(Employee).where(Employee.id.in_(visible_ids))
    else:
        query = select(Employee)

    if not include_terminated:
        query = query.where(Employee.employment_status != "terminated")

    result = await session.execute(query)
    return list(result.scalars().all())


async def _require_department_reference(session: AsyncSession, department_id: int) -> None:
    if await session.get(Department, department_id) is None:
        raise ValueError(f"department_id: department {department_id} not found")


async def _require_active_manager_reference(session: AsyncSession, manager_id: int) -> None:
    manager = await session.get(Employee, manager_id)
    if manager is None:
        raise ValueError(f"manager_id: employee {manager_id} not found")
    if manager.employment_status != "active":
        # Иначе поддерево подчинения указывает на неактивного человека,
        # и маршрут согласования (M3) упирается в никуда.
        raise ValueError("manager_id: a terminated employee cannot be a manager")


async def _require_personnel_number_free(
    session: AsyncSession, personnel_number: str, *, exclude_employee_id: int | None = None
) -> None:
    query = select(Employee.id).where(Employee.personnel_number == personnel_number)
    if exclude_employee_id is not None:
        query = query.where(Employee.id != exclude_employee_id)
    if (await session.execute(query.limit(1))).first() is not None:
        raise ValueError(f"personnel_number: '{personnel_number}' is already in use")


async def _require_email_free(
    session: AsyncSession, email: str, *, exclude_employee_id: int | None = None
) -> None:
    query = select(Employee.id).where(func.lower(Employee.email) == email.lower())
    if exclude_employee_id is not None:
        query = query.where(Employee.id != exclude_employee_id)
    if (await session.execute(query.limit(1))).first() is not None:
        raise ValueError(f"email: '{email}' is already in use")


async def create_employee(session: AsyncSession, payload: EmployeeCreate) -> Employee:
    await _require_department_reference(session, payload.department_id)
    if payload.manager_id is not None:
        await _require_active_manager_reference(session, payload.manager_id)
    await _require_personnel_number_free(session, payload.personnel_number)
    await _require_email_free(session, payload.email)

    password_hash: str | None = None
    if payload.auth_type == "local":
        # `_validate_initial_password` (schemas/employee.py) уже
        # гарантировал непустой пароль для `auth_type = "local"`.
        assert payload.initial_password is not None
        validate_password_strength(payload.initial_password.get_secret_value())
        password_hash = hash_password(payload.initial_password.get_secret_value())

    employee = Employee(
        full_name=payload.full_name,
        personnel_number=payload.personnel_number,
        department_id=payload.department_id,
        position=payload.position,
        hire_date=payload.hire_date,
        manager_id=payload.manager_id,
        email=payload.email,
        work_schedule_type=payload.work_schedule_type,
        leave_category=payload.leave_category,
    )
    session.add(employee)

    try:
        await session.flush()  # нужен employee.id для accounts.employee_id
        account = Account(
            employee_id=employee.id,
            auth_type=payload.auth_type,
            local_password_hash=password_hash,
            must_change_password=payload.auth_type == "local",
            role="employee",
            is_active=True,
        )
        session.add(account)
        await session.commit()
    except Exception:
        # Карточка и учётная запись — одна транзакция: сбой после вставки
        # карточки не должен оставить сотрудника, который не может войти
        # и которого нельзя дозавести (табельный номер/e-mail уже заняты).
        await session.rollback()
        raise

    await session.refresh(employee)
    return employee


def _apply_auth_type_change(account: Account, payload: EmployeeUpdate) -> None:
    """T-M1-34: смена способа входа сотрудника. Мутирует `account` в
    памяти — коммитится вместе с карточкой одним `session.commit()` в
    `update_employee`, той же гарантией атомарности, что и создание
    (`create_employee`)."""
    if payload.auth_type == "local" and account.auth_type != "local":
        # Переход LDAP -> локальный: обязателен первичный пароль,
        # проходящий парольную политику, со сменой при первом входе —
        # симметрично созданию карточки (`EmployeeCreate`, T-M1-25).
        has_password = (
            payload.new_password is not None and payload.new_password.get_secret_value().strip()
        )
        if not has_password:
            raise ValueError("new_password is required when switching auth_type to 'local'")
        assert payload.new_password is not None
        validate_password_strength(payload.new_password.get_secret_value())
        account.local_password_hash = hash_password(payload.new_password.get_secret_value())
        account.must_change_password = True
        account.auth_type = "local"
    elif payload.auth_type == "ldap" and account.auth_type != "ldap":
        # Переход локальный -> LDAP: удаляем сохранённый хэш пароля —
        # дальше вход допускается только через каталог (T-M1-34).
        account.local_password_hash = None
        account.must_change_password = False
        account.auth_type = "ldap"
    elif payload.auth_type == "local" and payload.new_password is not None:
        # Способ входа не меняется (остаётся локальным), но пароль всё
        # равно прислан — это уже не смена способа входа, а сброс
        # пароля, вне области T-M1-34 (для него — отдельный механизм,
        # T-M1-30). Отклоняем явно, а не молча меняем пароль мимо него.
        raise ValueError(
            "new_password is only accepted when switching auth_type from 'ldap' to 'local'"
        )


async def update_employee(
    session: AsyncSession, employee_id: int, payload: EmployeeUpdate
) -> Employee:
    employee = await session.get(Employee, employee_id)
    if employee is None:
        raise EmployeeNotFoundError(f"Employee {employee_id} not found")

    # Карточка и учётная запись существуют атомарно с момента создания
    # (`create_employee`) — `None` здесь означало бы, что этот инвариант
    # уже нарушен где-то ещё, а не штатный случай этого эндпоинта.
    account = await get_account_by_employee_id(session, employee.id)
    if account is None:
        raise ValueError(f"Employee {employee_id} has no linked account")

    await _require_department_reference(session, payload.department_id)
    if payload.manager_id is not None:
        if payload.manager_id == employee.id:
            raise ValueError("manager_id: an employee cannot be their own manager")
        await _require_active_manager_reference(session, payload.manager_id)
        # Цикл по цепочке подчинения: запрещаем, если сам сотрудник уже
        # стоит выше предлагаемого руководителя — иначе после правки он
        # оказался бы одновременно и предком, и потомком того же узла.
        if await is_in_management_chain(session, employee.id, payload.manager_id):
            raise ValueError("manager_id: would create a cycle in the reporting chain")

    await _require_personnel_number_free(
        session, payload.personnel_number, exclude_employee_id=employee.id
    )
    await _require_email_free(session, payload.email, exclude_employee_id=employee.id)
    _apply_auth_type_change(account, payload)

    employee.full_name = payload.full_name
    employee.personnel_number = payload.personnel_number
    employee.department_id = payload.department_id
    employee.position = payload.position
    employee.hire_date = payload.hire_date
    employee.manager_id = payload.manager_id
    employee.email = payload.email
    employee.work_schedule_type = payload.work_schedule_type
    employee.leave_category = payload.leave_category

    await session.commit()
    await session.refresh(employee)
    return employee
