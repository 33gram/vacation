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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.account import Account
from app.models.department import Department
from app.models.employee import Employee
from app.modules.auth.password_policy import validate_password_strength
from app.modules.auth.rbac import is_in_management_chain, list_subordinate_employee_ids
from app.modules.auth.repository import get_account_by_employee_id
from app.schemas.employee import (
    EmployeeCardFields,
    EmployeeCreate,
    EmployeeDeactivate,
    EmployeeUpdate,
)

# Имена полей — из `EmployeeCardFields` (schemas/employee.py), а не
# отдельный список здесь: раньше эти девять полей были перечислены
# четырежды (в `EmployeeCreate`, `EmployeeUpdate`, конструкторе
# `create_employee` и блоке присваиваний `update_employee`).
_CARD_FIELD_NAMES = tuple(EmployeeCardFields.model_fields)

# Роли, видящие ЛЮБУЮ карточку (`plan.md` §5.1); `accountant` — только
# чтение, ограничение на запись обеспечивает `require_roles` в роутере,
# а не эта проверка.
PRIVILEGED_VIEW_ROLES = frozenset({"hr_admin", "system_admin", "accountant"})

# Независимый ключ, симметричный `DEPARTMENT_TREE_LOCK_KEY`
# (`department_service.py`) — цепочка подчинения и дерево подразделений
# защищены независимо, общий ключ смешивал бы два разных инварианта.
MANAGEMENT_CHAIN_LOCK_KEY = 7_270_010_102


async def _lock_management_chain(session: AsyncSession) -> None:
    """Та же гонка и то же решение, что и
    `department_service._lock_department_tree` (см. её докстринг): под
    READ COMMITTED два конкурентных `PUT`, назначающих сотрудников
    руководителями друг друга, оба не видят цикла и оба коммитят своё
    ребро. `pg_advisory_xact_lock` сериализует конкурентные правки
    `manager_id` между собой; снимается коммитом/откатом текущей
    транзакции."""
    await session.execute(select(func.pg_advisory_xact_lock(MANAGEMENT_CHAIN_LOCK_KEY)))


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


# Источник истины — ограничение БД, а не проверка выше: под конкурентной
# нагрузкой два запроса могут пройти `_require_personnel_number_free`/
# `_require_email_free` одновременно (TOCTOU — обе проверки читают
# состояние до записи другого запроса), и тогда именно эта проверка при
# `COMMIT` ловит нарушение. Поле определяется по ИМЕНИ ОГРАНИЧЕНИЯ из
# диагностики asyncpg, а не разбором текста ошибки — имя не зависит от
# локали/версии сервера, текст сообщения зависит.
_UNIQUE_CONSTRAINT_FIELDS = {
    "employees_personnel_number_key": "personnel_number",
    "ix_employees_email_lower": "email",
}


def _translate_unique_violation(
    exc: IntegrityError, payload: EmployeeCreate | EmployeeUpdate
) -> Exception:
    """`ValueError` с тем же текстом и тем же полем, что и опережающая
    проверка (`_require_personnel_number_free`/`_require_email_free`) —
    клиент не должен различать «поймали до записи» и «поймали на
    гонке», поэтому оба пути дают один и тот же тип исключения (роутер
    превращает `ValueError` в 400 одинаково для обоих) и один и тот же
    текст. Ограничение, не входящее в карту (например, случайное
    нарушение внешнего ключа), не подменяется — исходная ошибка
    поднимается как есть, чтобы не маскировать неожиданный сбой под
    штатный отказ 400."""
    constraint_name = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)
    field = _UNIQUE_CONSTRAINT_FIELDS.get(constraint_name)
    if field == "personnel_number":
        return ValueError(f"personnel_number: '{payload.personnel_number}' is already in use")
    if field == "email":
        return ValueError(f"email: '{payload.email}' is already in use")
    return exc


def _apply_card_fields(employee: Employee, payload: EmployeeCardFields) -> None:
    """Копирует девять общих полей карточки (`_CARD_FIELD_NAMES`) из
    тела запроса в ORM-объект — общая для `create_employee` (на новый,
    ещё не добавленный в сессию объект) и `update_employee` (на
    существующий)."""
    for field_name in _CARD_FIELD_NAMES:
        setattr(employee, field_name, getattr(payload, field_name))


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

    employee = Employee()
    _apply_card_fields(employee, payload)
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
    except IntegrityError as exc:
        # Карточка и учётная запись — одна транзакция: откат отменяет и
        # вставку карточки тоже, сбой не оставляет сотрудника, которого
        # нельзя ни авторизовать, ни дозавести повторным `POST`.
        await session.rollback()
        raise _translate_unique_violation(exc, payload) from exc
    except Exception:
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
        await _lock_management_chain(session)
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

    _apply_card_fields(employee, payload)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _translate_unique_violation(exc, payload) from exc

    await session.refresh(employee)
    return employee


async def deactivate_employee(
    session: AsyncSession, employee_id: int, payload: EmployeeDeactivate
) -> Employee:
    """T-M1-12, FR-EMP-004 (без фиксации остатка — `T-M1-37`).

    Отказывает, если у сотрудника есть активные ПРЯМЫЕ подчинённые
    (`Employee.manager_id`) или он назначен руководителем подразделения
    (`departments.head_employee_id`) — симметрично тому, что
    `_require_active_manager_reference` уже запрещает НАЗНАЧАТЬ
    уволенного руководителем (`T-M1-15`): оставлять подчинённых висеть
    на уволенном — то же противоречие с другой стороны. Не проверяет
    всё поддерево (`list_subordinate_employee_ids`) — переподчинение
    прямых подчинённых уже разрывает цепочку до уволенного для всех,
    кто ниже них."""
    employee = await session.get(Employee, employee_id)
    if employee is None:
        raise EmployeeNotFoundError(f"Employee {employee_id} not found")
    if employee.employment_status == "terminated":
        raise ValueError(f"Employee {employee_id} is already terminated")

    account = await get_account_by_employee_id(session, employee.id)
    if account is None:
        raise ValueError(f"Employee {employee_id} has no linked account")

    direct_reports = (
        (
            await session.execute(
                select(Employee.id)
                .where(Employee.manager_id == employee.id)
                .where(Employee.employment_status == "active")
                .order_by(Employee.id)
            )
        )
        .scalars()
        .all()
    )
    headed_department_ids = (
        (
            await session.execute(
                select(Department.id)
                .where(Department.head_employee_id == employee.id)
                .order_by(Department.id)
            )
        )
        .scalars()
        .all()
    )

    problems = []
    if direct_reports:
        problems.append(
            f"reassign manager_id for active direct report(s) {list(direct_reports)} first"
        )
    if headed_department_ids:
        problems.append(
            "reassign head_employee_id for department(s) "
            f"{list(headed_department_ids)} first"
        )
    if problems:
        raise ValueError("cannot deactivate: " + "; ".join(problems))

    employee.employment_status = "terminated"
    employee.termination_date = payload.termination_date
    # Доступ и так закрыт статусом карточки через `enforce_access`
    # (T-M1-27), но два источника истины о том, работает ли человек, со
    # временем расходятся — `accounts.is_active` держится в согласии с
    # `employees.employment_status` явно, а не по выводу из него на
    # каждой проверке.
    account.is_active = False

    await session.commit()
    await session.refresh(employee)
    return employee
