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
from app.core.time_utils import utcnow
from app.models.account import Account
from app.models.department import Department
from app.models.employee import Employee
from app.modules.auth.password_policy import validate_password_strength
from app.modules.auth.rbac import is_in_management_chain, list_subordinate_employee_ids
from app.modules.auth.repository import get_account_by_employee_id
from app.modules.balances.service import recalculate_balance
from app.modules.business_rules.service import PASSWORD_MIN_LENGTH, get_int
from app.modules.employees.unique_violations import (
    email_conflict_message,
    personnel_number_conflict_message,
    translate_unique_violation,
)
from app.schemas.employee import (
    EmployeeCardFields,
    EmployeeCreate,
    EmployeeDeactivate,
    EmployeeOut,
    EmployeeUpdate,
)

# Имена полей — из `EmployeeCardFields` (schemas/employee.py), а не
# отдельный список здесь: раньше эти девять полей были перечислены
# четырежды (в `EmployeeCreate`, `EmployeeUpdate`, конструкторе
# `create_employee` и блоке присваиваний `update_employee`).
_CARD_FIELD_NAMES = tuple(EmployeeCardFields.model_fields)

# Поля карточки, смена которых наступает как `WHEN` критерия `FR-BAL-003` и
# вызывает пересчёт остатка (`recalculate_balance`): изменилось ЛЮБОЕ поле из
# набора - пересчёт вызывается (набор управляет условием в `update_employee`).
# Критерий называет ещё и категорию (`leave_category`), но СЕГОДНЯ норма
# отпуска от неё не зависит (удлинённый и дополнительный отпуск - за границами
# MVP, `A-3.1`), поэтому её здесь нет; когда норма начнёт зависеть от
# категории, её надо добавить сюда И научить `recalculate_balance` её учитывать
# (поле, добавленное только сюда, вызовет пересчёт, который ничего не запишет). Ставка
# занятости норму не меняет вовсе (ст. 93 ТК РФ, `requirements.md` v1.24).
BALANCE_RECALCULATION_FIELDS = ("hire_date",)

# Роли, видящие ЛЮБУЮ карточку (`plan.md` §5.1); `accountant` — только
# чтение, ограничение на запись обеспечивает `require_roles` в роутере,
# а не эта проверка.
PRIVILEGED_VIEW_ROLES = frozenset({"hr_admin", "system_admin", "accountant"})

# Независимый ключ, симметричный `DEPARTMENT_TREE_LOCK_KEY`
# (`department_service.py`) — цепочка подчинения и дерево подразделений
# защищены независимо, общий ключ смешивал бы два разных инварианта.
MANAGEMENT_CHAIN_LOCK_KEY = 7_270_010_102


async def lock_management_chain(session: AsyncSession) -> None:
    """Та же гонка и то же решение, что и
    `department_service._lock_department_tree` (см. её докстринг): под
    READ COMMITTED конкурентные операции, которые читают или меняют
    `employees.manager_id`/`employment_status`/`departments.head_
    employee_id`, могут разойтись на устаревших снимках друг друга.
    `pg_advisory_xact_lock` сериализует их между собой; снимается
    коммитом/откатом текущей транзакции.

    Публичная (без `_`): вызывается не только из этого модуля, но и из
    `department_service.update_department` (PR #139 review, замечание
    B) и `import_service.import_employees` (замечание C) — назначение
    руководителя подразделения и импорт трогают тот же инвариант
    («активен ли сотрудник сейчас»), что и назначение прямого
    руководителя и деактивация, поэтому все четыре пути сериализуются
    ОДНИМ ключом, а не отдельными."""
    await session.execute(select(func.pg_advisory_xact_lock(MANAGEMENT_CHAIN_LOCK_KEY)))


class EmployeeNotFoundError(Exception):
    """Карточка, которую правят (`PUT /api/employees/{id}`), отсутствует
    — 404, в отличие от некорректной ссылки в теле запроса (несуществующий
    `department_id`/`manager_id`, дубликат), которая остаётся
    `ValueError`/400 (см. `department_service.DepartmentNotFoundError`,
    тот же принцип)."""


class EmployeeAccountMissingError(Exception):
    """`T-M1-52`: карточка сотрудника без связанной строки `accounts`.
    Не должно происходить — `create_employee` и `import_service.
    import_employees` создают обе строки одной транзакцией, а строк
    `employees`/`accounts` в кодовой базе нигде не удаляют (проверено:
    ни одного `session.delete`/`DELETE FROM` за пределами тестов). Это
    гарантия соблюдения инварианта приложением, а НЕ ограничение БД —
    `ck_accounts_employee_or_login` ограничивает форму строки `accounts`
    (ровно одно из `employee_id`/`login`), а не то, что такая строка
    существует для каждого `employees.id`. Если это исключение всплыло —
    инвариант нарушен в обход API (например, прямым SQL), а не штатной
    работой сервиса."""


def _employee_out(employee: Employee, account: Account) -> EmployeeOut:
    return EmployeeOut(
        id=employee.id,
        full_name=employee.full_name,
        personnel_number=employee.personnel_number,
        department_id=employee.department_id,
        position=employee.position,
        hire_date=employee.hire_date,
        manager_id=employee.manager_id,
        email=employee.email,
        work_schedule_type=employee.work_schedule_type,
        employment_status=employee.employment_status,
        termination_date=employee.termination_date,
        leave_category=employee.leave_category,
        auth_type=account.auth_type,
    )


async def to_employee_out(session: AsyncSession, employee: Employee) -> EmployeeOut:
    """`T-M1-52`: собирает `EmployeeOut`, включая `auth_type` — живёт на
    связанной `accounts` строке, не на `employee`, поэтому здесь отдельный
    запрос, а не голый `EmployeeOut.model_validate(employee)`, как раньше.
    Роутер вызывает это для каждого ответа `EmployeeOut` (`get`/`post`/
    `put`/`deactivate`) — в путях правки/деактивации `account` уже был
    прочитан сервисом раньше по той же причине (`_apply_auth_type_change`,
    проверка перед деактивацией), этот запрос там избыточен, но не
    ошибочен: повторное чтение по уникальному индексу `accounts.
    employee_id` на штате до 750 человек (`NFR-SCALE-001`) не стоит того,
    чтобы ради него менять сигнатуру `update_employee`/`deactivate_
    employee` (обе возвращают `Employee`, а не пару с `Account`, и вызываются
    напрямую тестами)."""
    account = await get_account_by_employee_id(session, employee.id)
    if account is None:
        raise EmployeeAccountMissingError(f"У сотрудника {employee.id} нет учётной записи")
    return _employee_out(employee, account)


async def to_employee_out_list(
    session: AsyncSession, employees: list[Employee]
) -> list[EmployeeOut]:
    """Пакетный вариант `to_employee_out` для `GET /api/employees` — один
    запрос счётов на весь список, а не N+1 на штате до 750 человек
    (`NFR-SCALE-001`)."""
    if not employees:
        return []
    employee_ids = [employee.id for employee in employees]
    result = await session.execute(select(Account).where(Account.employee_id.in_(employee_ids)))
    accounts_by_employee_id = {account.employee_id: account for account in result.scalars().all()}

    out: list[EmployeeOut] = []
    for employee in employees:
        account = accounts_by_employee_id.get(employee.id)
        if account is None:
            raise EmployeeAccountMissingError(f"У сотрудника {employee.id} нет учётной записи")
        out.append(_employee_out(employee, account))
    return out


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
        raise ValueError(f"department_id: подразделение {department_id} не найдено")


async def _require_active_manager_reference(session: AsyncSession, manager_id: int) -> None:
    manager = await session.get(Employee, manager_id)
    if manager is None:
        raise ValueError(f"manager_id: сотрудник {manager_id} не найден")
    if manager.employment_status != "active":
        # Иначе поддерево подчинения указывает на неактивного человека,
        # и маршрут согласования (M3) упирается в никуда.
        raise ValueError("manager_id: уволенный сотрудник не может быть руководителем")


async def _require_personnel_number_free(
    session: AsyncSession, personnel_number: str, *, exclude_employee_id: int | None = None
) -> None:
    query = select(Employee.id).where(Employee.personnel_number == personnel_number)
    if exclude_employee_id is not None:
        query = query.where(Employee.id != exclude_employee_id)
    if (await session.execute(query.limit(1))).first() is not None:
        raise ValueError(personnel_number_conflict_message(personnel_number))


async def _require_email_free(
    session: AsyncSession, email: str, *, exclude_employee_id: int | None = None
) -> None:
    query = select(Employee.id).where(func.lower(Employee.email) == email.lower())
    if exclude_employee_id is not None:
        query = query.where(Employee.id != exclude_employee_id)
    if (await session.execute(query.limit(1))).first() is not None:
        raise ValueError(email_conflict_message(email))


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
        # У карточки сотрудника нет отдельного логина (вход — по e-mail,
        # `auth/router.py`), поэтому `login` контексту не передаётся.
        validate_password_strength(
            payload.initial_password.get_secret_value(),
            min_length=await get_int(session, PASSWORD_MIN_LENGTH),
            email=payload.email,
            full_name=payload.full_name,
        )
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
        raise translate_unique_violation(
            exc, personnel_number=payload.personnel_number, email=payload.email
        ) from exc
    except Exception:
        await session.rollback()
        raise

    await session.refresh(employee)
    return employee


async def _apply_auth_type_change(
    session: AsyncSession, account: Account, payload: EmployeeUpdate
) -> None:
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
            raise ValueError("Пароль обязателен при переходе на локальный вход")
        assert payload.new_password is not None
        validate_password_strength(
            payload.new_password.get_secret_value(),
            min_length=await get_int(session, PASSWORD_MIN_LENGTH),
            email=payload.email,
            full_name=payload.full_name,
        )
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
            "Пароль принимается только при переходе с LDAP на локальный вход"
        )


async def update_employee(
    session: AsyncSession,
    employee_id: int,
    payload: EmployeeUpdate,
    *,
    actor_account_id: int,
) -> Employee:
    """Правка карточки. Если изменилось поле из `BALANCE_RECALCULATION_FIELDS`
    (дата приёма), в ТОЙ ЖЕ транзакции выполняется пересчёт остатка
    (`FR-BAL-003`, `T-M2-07`) от имени `actor_account_id`; отказ пересчёта
    (`BalanceEntryError`) уходит вызывающему, и `commit` не вызывается (проверено
    тестом); что правка карточки при этом не сохраняется, обеспечивает закрытие
    сессии без фиксации в `get_db_session` - в тестах сессия общая, там это не
    проверяется. К журналу
    остатка модуль обращается только через функции `balances`."""
    employee = await session.get(Employee, employee_id)
    if employee is None:
        raise EmployeeNotFoundError(f"Сотрудник {employee_id} не найден")

    # Карточка и учётная запись существуют атомарно с момента создания
    # (`create_employee`) — `None` здесь означало бы, что этот инвариант
    # уже нарушен где-то ещё, а не штатный случай этого эндпоинта.
    account = await get_account_by_employee_id(session, employee.id)
    if account is None:
        raise ValueError(f"У сотрудника {employee_id} нет привязанной учётной записи")

    await _require_department_reference(session, payload.department_id)
    if payload.manager_id is not None:
        if payload.manager_id == employee.id:
            raise ValueError("manager_id: сотрудник не может быть руководителем самому себе")
        await lock_management_chain(session)
        await _require_active_manager_reference(session, payload.manager_id)
        # Цикл по цепочке подчинения: запрещаем, если сам сотрудник уже
        # стоит выше предлагаемого руководителя — иначе после правки он
        # оказался бы одновременно и предком, и потомком того же узла.
        if await is_in_management_chain(session, employee.id, payload.manager_id):
            raise ValueError("manager_id: создаст цикл в цепочке подчинения")

    await _require_personnel_number_free(
        session, payload.personnel_number, exclude_employee_id=employee.id
    )
    await _require_email_free(session, payload.email, exclude_employee_id=employee.id)
    await _apply_auth_type_change(session, account, payload)

    # Строка сотрудника берётся под замок ЗДЕСЬ - после цепочки подчинения (если
    # она была нужна), перед чтением "было". Порядок "цепочка -> строка" тот же,
    # что у `deactivate_employee`, `import_employees` и `update_department`
    # (они берут цепочку до записи строк); замок строки в начале функции дал бы
    # обратный порядок и взаимную блокировку. До этого места объект `employee`
    # не изменялся (меняются `account` и, ниже, поля карточки), поэтому
    # `refresh` не затирает несохранённых правок и даёт последнее зафиксированное
    # значение. Нужен именно `refresh`: `session.get(..., with_for_update=True)`
    # для уже загруженного объекта атрибуты не обновляет.
    await session.refresh(employee, with_for_update=True)
    previous = {name: getattr(employee, name) for name in BALANCE_RECALCULATION_FIELDS}
    _apply_card_fields(employee, payload)

    changes = {
        name: (previous[name], getattr(employee, name))
        for name in BALANCE_RECALCULATION_FIELDS
        if getattr(employee, name) != previous[name]
    }
    if changes:
        await recalculate_balance(
            session,
            employee_id=employee.id,
            actor_account_id=actor_account_id,
            # Дата расчёта - из единого источника времени проекта (UTC), как и
            # остальные метки системы; бизнес-дата в местном поясе - отдельное
            # решение, не принятое здесь.
            as_of=utcnow().date(),
            changes=changes,
        )

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise translate_unique_violation(
            exc, personnel_number=payload.personnel_number, email=payload.email
        ) from exc

    await session.refresh(employee)
    return employee


async def _require_no_active_dependents(session: AsyncSession, employee: Employee) -> None:
    """Отказывает деактивации, если у `employee` есть активные ПРЯМЫЕ
    подчинённые (`Employee.manager_id`) или он назначен руководителем
    подразделения (`departments.head_employee_id`) — симметрично тому,
    что `_require_active_manager_reference` уже запрещает НАЗНАЧАТЬ
    уволенного руководителем (`T-M1-15`): оставлять подчинённых висеть
    на уволенном — то же противоречие с другой стороны. Не проверяет
    всё поддерево (`list_subordinate_employee_ids`) — переподчинение
    прямых подчинённых уже разрывает цепочку до уволенного для всех,
    кто ниже них.

    Проверяет ДВА независимых предмета, и у каждого своя история
    защиты от гонки с этой проверкой (PR #139 review, замечание 2 оси
    стандартов — этот докстринг раньше выдавал `lock_management_chain`
    за проверку целиком, хотя блокировка тогда закрывала только
    первый):
    - `manager_id` — `update_employee` берёт `lock_management_chain`
      перед назначением руководителя с самого начала (PR #138);
    - `head_employee_id` — `update_department` стал брать ту же
      блокировку перед назначением руководителя подразделения только
      в PR #139 review (замечание B); до этого назначить уже
      уволенного сотрудника руководителем подразделения не мешало
      ничто, включая эту проверку.

    С обоими путями под одной блокировкой (плюс `import_service.
    import_employees`, замечание C — третий писатель `manager_id`)
    вызывающая сторона обязана взять `lock_management_chain` ДО этой
    проверки, как и делает `deactivate_employee`, — тогда гонка с ЛЮБЫМ
    из трёх писателей закрыта. Остаётся не закрытым только то, что не
    может закрыть ни один замок на уровне приложения: прямая правка
    `employees`/`departments` в обход API (например, будущий импорт
    остатков или ручной SQL) не берёт эту блокировку и может оставить
    то же противоречие — тот же класс ограничения, что и у проверки
    циклов в `department_service._would_create_cycle`."""
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
            "сначала переназначьте manager_id для активных прямых подчинённых "
            f"{list(direct_reports)}"
        )
    if headed_department_ids:
        problems.append(
            "сначала переназначьте head_employee_id для подразделений "
            f"{list(headed_department_ids)}"
        )
    if problems:
        raise ValueError("Невозможно деактивировать: " + "; ".join(problems))


async def deactivate_employee(
    session: AsyncSession, employee_id: int, payload: EmployeeDeactivate
) -> Employee:
    """T-M1-12, FR-EMP-004 (без фиксации остатка — `T-M1-37`). Критерий
    отказа при активных зависимостях — `_require_no_active_dependents`."""
    employee = await session.get(Employee, employee_id)
    if employee is None:
        raise EmployeeNotFoundError(f"Сотрудник {employee_id} не найден")
    if employee.employment_status == "terminated":
        raise ValueError(f"Сотрудник {employee_id} уже уволен")

    account = await get_account_by_employee_id(session, employee.id)
    if account is None:
        raise ValueError(f"У сотрудника {employee_id} нет привязанной учётной записи")

    # PR #139 review, verification item 4: same write skew PR #138 closed
    # for manager_id cycles, on a different pair of paths. Under READ
    # COMMITTED, a concurrent PUT assigning employee.id as someone's
    # manager_id reads "employee is active" before this transaction's
    # commit, while this transaction's dependents check reads "no active
    # reports yet" before that PUT's commit — both pass, leaving a
    # terminated employee with an active direct report. Taking the same
    # lock update_employee's manager-assignment path already takes,
    # BEFORE the dependents check, serializes the two: whichever commits
    # first is seen by the other.
    await lock_management_chain(session)
    await _require_no_active_dependents(session, employee)

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
