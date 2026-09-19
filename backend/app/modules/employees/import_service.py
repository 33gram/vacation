"""Сервис импорта сотрудников (T-M1-11, FR-EMP-003).

ДВУХФАЗНО — это следствие двух требований сразу, а не деталь реализации.
`FR-EMP-003` требует не прерывать загрузку корректных строк построчным
падением; запрет циклов в `manager_id`, наоборот, — свойство НАБОРА
строк, а не отдельной. Построчный коммит не может удовлетворить оба
требования: коммитить строку раньше, чем проверен граф всего файла,
значит рисковать зафиксировать половину будущего цикла.

Фаза 1 (без записи): построчная валидация (обязательные поля, форматы,
подразделение по имени, уникальность `personnel_number`/`email`
относительно БД И между строками файла), затем разрешение ссылок на
руководителей по всему файлу сразу и проверка графа подчинения —
существующего в БД плюс принимаемых строк — на циклы. Фаза 2: одной
транзакцией пишет только то, что прошло фазу 1, вместе с учётными
записями.

Решения по импорту (создаёт, не обновляет; руководитель — по
`personnel_number`; подразделение — по имени, не создаётся; способ
входа всегда LDAP; файл не сохраняется) — `plan.md` §6.

ПЕРСОНАЛЬНЫЕ ДАННЫЕ: отчёт (`ImportResult`) возвращается загрузившему
его HR-администратору, которому эти данные и так доступны — значения
полей в отчёте допустимы. Этот модуль ничего не логирует сам; вызывающий
код (`employee_router.py`) обязан логировать только количества и номера
строк, не значения полей (см. его докстринг).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.department import Department
from app.models.employee import WORK_SCHEDULE_TYPES, Employee
from app.modules.auth.rbac import MAX_MANAGEMENT_CHAIN_DEPTH
from app.modules.employees.import_parsing import ParsedRow

_REQUIRED_FIELDS = (
    "full_name",
    "personnel_number",
    "department",
    "position",
    "hire_date",
    "email",
    "work_schedule_type",
)

# Разрешённый тип значения manager_target: ссылка на другую строку файла
# (по personnel_number) или на уже существующего в БД сотрудника (по id);
# `None` — вершина иерархии (пусто в файле).
_ManagerTarget = tuple[str, str] | tuple[str, int] | None


@dataclass(frozen=True)
class RowError:
    row_number: int
    field: str
    reason: str


@dataclass(frozen=True)
class ImportResult:
    accepted_count: int
    rejected_count: int
    errors: list[RowError]


@dataclass
class _Candidate:
    row_number: int
    full_name: str
    personnel_number: str
    department_id: int
    position: str
    hire_date: datetime.date
    email: str
    work_schedule_type: str
    manager_personnel_number: str | None
    leave_category: str | None


async def _load_existing(session: AsyncSession):
    result = await session.execute(
        select(
            Employee.id,
            Employee.personnel_number,
            Employee.manager_id,
            Employee.employment_status,
            Employee.email,
        )
    )
    rows = result.all()
    return {
        "personnel_numbers": {r.personnel_number for r in rows},
        "id_by_personnel_number": {r.personnel_number: r.id for r in rows},
        "manager_by_id": {r.id: r.manager_id for r in rows},
        "status_by_id": {r.id: r.employment_status for r in rows},
        "emails_lower": {r.email.lower() for r in rows},
    }


async def _load_departments_by_name(session: AsyncSession) -> dict[str, list[int]]:
    result = await session.execute(select(Department.id, Department.name))
    by_name: dict[str, list[int]] = {}
    for row in result.all():
        by_name.setdefault(row.name, []).append(row.id)
    return by_name


def _validate_rows(
    rows: list[ParsedRow],
    *,
    existing: dict,
    departments_by_name: dict[str, list[int]],
) -> tuple[dict[str, _Candidate], list[RowError]]:
    errors: list[RowError] = []
    candidates: dict[str, _Candidate] = {}
    seen_personnel_numbers: dict[str, int] = {}
    seen_emails_lower: dict[str, int] = {}

    for row in rows:
        row_errors: list[RowError] = []
        values = row.values

        missing = [f for f in _REQUIRED_FIELDS if not values.get(f)]
        for field_name in missing:
            row_errors.append(RowError(row.row_number, field_name, "required field is empty"))

        full_name = values.get("full_name")
        personnel_number = values.get("personnel_number")
        department_name = values.get("department")
        position = values.get("position")
        hire_date_raw = values.get("hire_date")
        email = values.get("email")
        work_schedule_type = values.get("work_schedule_type")
        manager_personnel_number = values.get("manager_personnel_number")
        leave_category = values.get("leave_category")

        hire_date: datetime.date | None = None
        if hire_date_raw is not None:
            try:
                hire_date = datetime.date.fromisoformat(hire_date_raw)
            except ValueError:
                row_errors.append(
                    RowError(row.row_number, "hire_date", f"invalid date: '{hire_date_raw}'")
                )

        if work_schedule_type is not None and work_schedule_type not in WORK_SCHEDULE_TYPES:
            row_errors.append(
                RowError(
                    row.row_number,
                    "work_schedule_type",
                    f"must be one of {sorted(WORK_SCHEDULE_TYPES)}",
                )
            )

        department_id: int | None = None
        if department_name is not None:
            matches = departments_by_name.get(department_name, [])
            if not matches:
                row_errors.append(
                    RowError(
                        row.row_number,
                        "department",
                        f"department '{department_name}' not found",
                    )
                )
            elif len(matches) > 1:
                row_errors.append(
                    RowError(
                        row.row_number,
                        "department",
                        f"department name '{department_name}' is ambiguous "
                        f"(department ids: {sorted(matches)})",
                    )
                )
            else:
                department_id = matches[0]

        if personnel_number is not None:
            if personnel_number in existing["personnel_numbers"]:
                row_errors.append(
                    RowError(
                        row.row_number,
                        "personnel_number",
                        f"'{personnel_number}' already exists",
                    )
                )
            elif personnel_number in seen_personnel_numbers:
                row_errors.append(
                    RowError(
                        row.row_number,
                        "personnel_number",
                        f"duplicated in file (also row "
                        f"{seen_personnel_numbers[personnel_number]})",
                    )
                )
            else:
                seen_personnel_numbers[personnel_number] = row.row_number

        if email is not None:
            email_lower = email.lower()
            if email_lower in existing["emails_lower"]:
                row_errors.append(
                    RowError(row.row_number, "email", f"'{email}' already exists")
                )
            elif email_lower in seen_emails_lower:
                row_errors.append(
                    RowError(
                        row.row_number,
                        "email",
                        f"duplicated in file (also row {seen_emails_lower[email_lower]})",
                    )
                )
            else:
                seen_emails_lower[email_lower] = row.row_number

        if row_errors:
            errors.extend(row_errors)
            continue

        assert full_name is not None
        assert personnel_number is not None
        assert position is not None
        assert hire_date is not None
        assert email is not None
        assert work_schedule_type is not None
        assert department_id is not None
        candidates[personnel_number] = _Candidate(
            row_number=row.row_number,
            full_name=full_name,
            personnel_number=personnel_number,
            department_id=department_id,
            position=position,
            hire_date=hire_date,
            email=email,
            work_schedule_type=work_schedule_type,
            manager_personnel_number=manager_personnel_number,
            leave_category=leave_category,
        )

    return candidates, errors


def _resolve_manager_targets(
    candidates: dict[str, _Candidate], existing: dict, errors: list[RowError]
) -> dict[str, _ManagerTarget]:
    manager_target: dict[str, _ManagerTarget] = {}
    for pn, candidate in list(candidates.items()):
        ref = candidate.manager_personnel_number
        if ref is None:
            manager_target[pn] = None
        elif ref in candidates:
            manager_target[pn] = ("file", ref)
        elif ref in existing["id_by_personnel_number"]:
            existing_id = existing["id_by_personnel_number"][ref]
            if existing["status_by_id"].get(existing_id) != "active":
                errors.append(
                    RowError(
                        candidate.row_number,
                        "manager_personnel_number",
                        f"'{ref}' refers to a terminated employee",
                    )
                )
                del candidates[pn]
                continue
            manager_target[pn] = ("existing", existing_id)
        else:
            errors.append(
                RowError(
                    candidate.row_number,
                    "manager_personnel_number",
                    f"'{ref}' not found among existing employees or valid rows in this file",
                )
            )
            del candidates[pn]
    return manager_target


def _find_file_cycles(
    candidates: dict[str, _Candidate], manager_target: dict[str, _ManagerTarget]
) -> set[str]:
    """DFS с явным стеком (не рекурсией — до `MAX_ROWS` узлов) по рёбрам
    `("file", ...)`: ссылки на существующих сотрудников не могут быть
    частью НОВОГО цикла — импорт только создаёт строки, ничего не
    обновляет, поэтому ни одна уже существующая запись не может
    получить ребро назад ни к одной строке файла."""
    UNVISITED, VISITING, DONE = 0, 1, 2
    state: dict[str, int] = dict.fromkeys(candidates, UNVISITED)
    cycle_members: set[str] = set()

    for start_pn in candidates:
        if state[start_pn] != UNVISITED:
            continue
        path: list[str] = []
        pn = start_pn
        while True:
            if state[pn] == DONE:
                break
            if state[pn] == VISITING:
                idx = path.index(pn)
                cycle_members.update(path[idx:])
                break
            state[pn] = VISITING
            path.append(pn)
            target = manager_target.get(pn)
            if target is None or target[0] != "file":
                break
            pn = target[1]
        for visited_pn in path:
            state[visited_pn] = DONE

    return cycle_members


def _existing_chain_is_healthy(
    start_id: int, existing_manager_by_id: dict[int, int | None]
) -> bool:
    """Существующая цепочка `manager_id` замкнута в цикл или превышает
    `MAX_MANAGEMENT_CHAIN_DEPTH` (та же защита, что и у `is_in_management_
    chain`, `rbac.py`) — на здоровых данных не сработает никогда; ловит
    попадание строки файла в уже испорченный граф (T-M1-11, tasks.md)."""
    current: int | None = start_id
    seen: set[int] = set()
    while current is not None:
        if current in seen or len(seen) > MAX_MANAGEMENT_CHAIN_DEPTH:
            return False
        seen.add(current)
        current = existing_manager_by_id.get(current)
    return True


def _reject_broken_chains(
    candidates: dict[str, _Candidate],
    manager_target: dict[str, _ManagerTarget],
    cycle_members: set[str],
    existing_manager_by_id: dict[int, int | None],
    errors: list[RowError],
) -> set[str]:
    """Замыкает фиксированную точку: строка, чей руководитель — уже
    отклонённая строка (цикл или каскад от цикла), тоже отклоняется —
    её `manager_id` иначе указывал бы на никогда не записанную строку.
    Отдельно — строка, чей руководитель ведёт в уже испорченный
    (циклический) существующий граф."""
    healthy_cache: dict[int, bool] = {}

    def existing_healthy(employee_id: int) -> bool:
        if employee_id not in healthy_cache:
            healthy_cache[employee_id] = _existing_chain_is_healthy(
                employee_id, existing_manager_by_id
            )
        return healthy_cache[employee_id]

    rejected: set[str] = set(cycle_members)
    for pn in cycle_members:
        candidate = candidates[pn]
        errors.append(
            RowError(
                candidate.row_number,
                "manager_personnel_number",
                f"forms a manager_id cycle with row(s) for personnel_number "
                f"{sorted(cycle_members)}",
            )
        )

    changed = True
    while changed:
        changed = False
        for pn, candidate in candidates.items():
            if pn in rejected:
                continue
            target = manager_target.get(pn)
            if target is None:
                continue
            kind, value = target
            if kind == "file" and value in rejected:
                rejected.add(pn)
                errors.append(
                    RowError(
                        candidate.row_number,
                        "manager_personnel_number",
                        f"'{value}' was rejected, so this row cannot reference it either",
                    )
                )
                changed = True
            elif kind == "existing" and not existing_healthy(value):
                rejected.add(pn)
                errors.append(
                    RowError(
                        candidate.row_number,
                        "manager_personnel_number",
                        f"management chain through existing employee id {value} "
                        "contains a cycle or exceeds the maximum depth",
                    )
                )
                changed = True

    return rejected


async def _write_accepted(
    session: AsyncSession,
    accepted: list[_Candidate],
    manager_target: dict[str, _ManagerTarget],
) -> None:
    employees_by_pn: dict[str, Employee] = {}
    for candidate in accepted:
        employee = Employee(
            full_name=candidate.full_name,
            personnel_number=candidate.personnel_number,
            department_id=candidate.department_id,
            position=candidate.position,
            hire_date=candidate.hire_date,
            manager_id=None,
            email=candidate.email,
            work_schedule_type=candidate.work_schedule_type,
            leave_category=candidate.leave_category,
        )
        session.add(employee)
        employees_by_pn[candidate.personnel_number] = employee

    # Нужны id: для ссылок на другие строки файла (manager_id) и для
    # accounts.employee_id — до COMMIT, одной транзакцией с учётными
    # записями (симметрично `employee_service.create_employee`).
    await session.flush()

    for candidate in accepted:
        target = manager_target.get(candidate.personnel_number)
        if target is None:
            continue
        kind, value = target
        employee = employees_by_pn[candidate.personnel_number]
        employee.manager_id = value if kind == "existing" else employees_by_pn[value].id

    session.add_all(
        Account(
            employee_id=employees_by_pn[candidate.personnel_number].id,
            auth_type="ldap",
            role="employee",
            is_active=True,
        )
        for candidate in accepted
    )

    await session.commit()


async def import_employees(session: AsyncSession, rows: list[ParsedRow]) -> ImportResult:
    existing = await _load_existing(session)
    departments_by_name = await _load_departments_by_name(session)

    candidates, errors = _validate_rows(
        rows, existing=existing, departments_by_name=departments_by_name
    )
    manager_target = _resolve_manager_targets(candidates, existing, errors)
    cycle_members = _find_file_cycles(candidates, manager_target)
    rejected = _reject_broken_chains(
        candidates, manager_target, cycle_members, existing["manager_by_id"], errors
    )

    accepted = [candidate for pn, candidate in candidates.items() if pn not in rejected]
    if accepted:
        await _write_accepted(session, accepted, manager_target)

    accepted_row_numbers = {c.row_number for c in accepted}
    rejected_count = len(rows) - len(accepted_row_numbers)

    return ImportResult(
        accepted_count=len(accepted),
        rejected_count=rejected_count,
        errors=sorted(errors, key=lambda e: (e.row_number, e.field)),
    )
