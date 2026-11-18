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

КОНКУРЕНТНАЯ ЗАПИСЬ (PR #139 review, замечание 1): фаза 1 проверяет
уникальность `personnel_number`/`email` по снимку БД, прочитанному ДО
записи, — под конкурентной нагрузкой два импорта могут пройти эту
проверку одновременно с общим значением (то же TOCTOU-окно, что и у
`_require_personnel_number_free`/`_require_email_free` в
`employee_service.py`). Проигравшая сторона узнаёт об этом только на
`flush`/`COMMIT`, когда откатывать уже поздно менять решение по ОДНОЙ
строке — вставка новых сотрудников идёт одним batched `INSERT`
(SQLAlchemy 2.x insertmanyvalues), и по имени нарушенного ограничения
нельзя определить, какая именно строка из пачки его нарушила (это и
не проверка текста ошибки — см. `unique_violations.py`, — а то, что
`IntegrityError` в принципе не привязан к конкретной строке batched
`INSERT`). Поэтому при нарушении ОДНОГО из известных ограничений
(`personnel_number`/`email`) фаза 1+2 повторяется целиком со свежим
снимком БД — вторая попытка увидит уже закоммиченную строку конкурента
как СУЩЕСТВУЮЩУЮ и штатно отклонит именно её через тот же путь, что и
обычный (не гоночный) повтор загрузки того же файла, приняв всё
остальное. Нарушение ограничения, не входящего в карту (`unique_
violations.UNIQUE_CONSTRAINT_FIELDS`), — не гонка на известном поле, а
неожиданный сбой; такое не повторяется и поднимается как есть.

ПЕРСОНАЛЬНЫЕ ДАННЫЕ: отчёт (`ImportResult`) возвращается загрузившему
его HR-администратору, которому эти данные и так доступны — значения
полей в отчёте допустимы. Этот модуль ничего не логирует сам; вызывающий
код (`employee_router.py`) обязан логировать только количества и номера
строк, не значения полей (см. его докстринг).

БЛОКИРОВКА `manager_id`/`head_employee_id` (PR #139 review, замечание
C): `_load_existing` читает `employment_status` существующих
сотрудников СНИМКОМ, а `_write_accepted` проставляет `manager_id`
принятым строкам по этому снимку, не перечитывая его перед записью —
тот же write skew, что уже закрыт `lock_management_chain` для
`update_employee` (назначение руководителя), `deactivate_employee` и
`update_department` (назначение руководителя подразделения), просто на
третьем/четвёртом пути: конкурентная деактивация сотрудника, снимок
которого импорт уже прочитал как "активен", может закоммитить
увольнение ПОСЛЕ чтения, но ДО записи импортом строки, ссылающейся на
него как на руководителя.

Блокировка берётся на всю фазу 1+2 текущей попытки (от `_load_existing`
до `_write_accepted` включительно), а не только вокруг проверки графа
подчинения: сама проблема — в том, что снимок статусов, прочитанный ДО
блокировки, устареет к моменту записи, если блокировка не накрывает и
чтение снимка тоже. Проверка графа (`_find_file_cycles`/`_reject_
broken_chains`) работает над данными, уже загруженными в память, — её
одной обернуть недостаточно, устаревшим остаётся сам снимок. Цена —
импорт (нечастая, HR-инициированная пакетная операция, как и правки
оргструктуры, которые эта блокировка уже сериализует) держит блокировку
дольше одного `PUT`, что здесь приемлемо по той же причине, что и для
них: сериализовать редкие операции дешевле, чем ловить конфликты
`SERIALIZABLE` с повтором на каждый запрос. Блокировка снимается
`COMMIT`/`ROLLBACK` текущей транзакции — на повторную попытку (см.
`_MAX_WRITE_ATTEMPTS` выше) берётся заново.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, NamedTuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.department import Department
from app.models.employee import WORK_SCHEDULE_TYPES, Employee
from app.models.leave_type import LeaveType
from app.modules.auth.rbac import MAX_MANAGEMENT_CHAIN_DEPTH
from app.modules.balances.service import BalanceEntryError, import_initial_balance
from app.modules.business_rules.business_day import business_today
from app.modules.employees.employee_service import lock_management_chain
from app.modules.employees.import_parsing import BALANCE_COLUMNS, ImportFileError, ParsedRow
from app.modules.employees.unique_violations import unique_violation_field

# Гонка на personnel_number/email (см. докстринг модуля) сходится за
# одну повторную попытку почти всегда — второй снимок БД уже видит
# конкурента. Второй запас — на случай, если конкурентов не два, а
# больше; после исчерпания — явный отказ, а не бесконечный повтор.
_MAX_WRITE_ATTEMPTS = 3

_REQUIRED_FIELDS = (
    "full_name",
    "personnel_number",
    "department",
    "position",
    "hire_date",
    "email",
    "work_schedule_type",
)

class ManagerTarget(NamedTuple):
    """Разрешённая ссылка на руководителя: другая строка файла (по
    `personnel_number`) или уже существующий в БД сотрудник (по id).
    `None` (не значение этого типа, а отсутствие ссылки вовсе) —
    вершина иерархии (пусто в файле).

    PR #139 review, замечание 3 оси стандартов: тег ("file"/"existing")
    раньше разбирался независимо в трёх функциях (`_find_file_cycles`,
    `_reject_broken_chains`, `_write_accepted`) — три копии одного и
    того же `if kind == "file" ... elif kind == "existing" ...`. Теперь
    разбор тега — только здесь, в двух свойствах ниже; вызывающий код
    спрашивает "это ссылка на строку файла?"/"на существующего
    сотрудника?", а не сравнивает строковый тег."""

    kind: Literal["file", "existing"]
    value: str | int

    @property
    def file_personnel_number(self) -> str | None:
        return self.value if self.kind == "file" else None

    @property
    def existing_employee_id(self) -> int | None:
        return self.value if self.kind == "existing" else None


_ManagerTarget = ManagerTarget | None


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
    # Номера принятых строк с приёмом раньше дня импорта и без начального остатка по основному
    # виду (`FR-EMP-003/7`); только номера - ни ФИО, ни e-mail, ни табельных номеров.
    warning_rows: list[int]


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
    # Начальные остатки по КОДУ вида: только строго положительные (ноль и пусто - события нет).
    balances: dict[str, Decimal]


# Основной вид: по нему идёт начисление, поэтому отсутствие остатка именно по нему даёт
# предупреждение (`FR-EMP-003/7`, раздел 13 п. 43, выбор (а)).
_MAIN_LEAVE_TYPE_CODE = "annual_paid"
# `Numeric(7, 2)` колонки `quantity`: не больше пяти знаков до запятой и двух после.
_BALANCE_NUMBER = re.compile(r"^[0-9]{1,5}([.,][0-9]{1,2})?$")


def _parse_balance(raw: str) -> tuple[Decimal | None, str | None]:
    """(значение, причина отказа). Десятичный разделитель - запятая или точка; минус в файле -
    ошибка (`FR-EMP-003/6`: скорее ошибка выгрузки, чем факт). Не больше двух знаков после
    разделителя и пяти до него - иначе значение не легло бы в `Numeric(7, 2)`."""
    text = raw.strip()
    if text.startswith("="):
        return None, "формула без сохранённого значения: сохраните файл со значениями"
    if text.startswith("-"):
        return None, f"отрицательный остаток «{raw}»: исправляется ручной корректировкой"
    if not _BALANCE_NUMBER.fullmatch(text):
        return None, (
            f"некорректный остаток «{raw}»: нужно число, не больше двух знаков после "
            "запятой и пяти до неё"
        )
    return Decimal(text.replace(",", ".")), None


async def _load_tracked_leave_type_ids(session: AsyncSession) -> dict[str, int]:
    """Код -> id видов, по которым ведётся остаток (положительное значение в такой колонке
    принимается только по ним): проверяется в фазе 1, чтобы запись остатка в фазе 2 не могла
    отказать - отказ после создания карточек отклонил бы весь импорт, а не строку."""
    result = await session.execute(
        select(LeaveType.code, LeaveType.id).where(LeaveType.balance_tracked.is_(True))
    )
    return {row.code: row.id for row in result.all()}


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
    today: datetime.date,
    tracked_leave_types: dict[str, int],
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
            row_errors.append(
                RowError(row.row_number, field_name, "обязательное поле не заполнено")
            )

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
                    RowError(row.row_number, "hire_date", f"некорректная дата: «{hire_date_raw}»")
                )

        balances: dict[str, Decimal] = {}
        for column, leave_type_code in BALANCE_COLUMNS.items():
            raw_balance = values.get(column)
            if raw_balance is None:
                continue
            amount, problem = _parse_balance(raw_balance)
            if problem is None and amount is not None and amount > 0:
                if leave_type_code not in tracked_leave_types:
                    problem = (
                        f"по виду отпуска «{leave_type_code}» остаток не ведётся: "
                        "оставьте колонку пустой"
                    )
                elif hire_date is not None and hire_date > today:
                    problem = (
                        "остаток на дату импорта невозможен: дата приёма позже дня импорта"
                    )
            if problem is not None:
                row_errors.append(RowError(row.row_number, column, problem))
            elif amount is not None and amount > 0:
                balances[leave_type_code] = amount

        if work_schedule_type is not None and work_schedule_type not in WORK_SCHEDULE_TYPES:
            row_errors.append(
                RowError(
                    row.row_number,
                    "work_schedule_type",
                    f"допустимые значения: {sorted(WORK_SCHEDULE_TYPES)}",
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
                        f"подразделение «{department_name}» не найдено",
                    )
                )
            elif len(matches) > 1:
                row_errors.append(
                    RowError(
                        row.row_number,
                        "department",
                        f"название подразделения «{department_name}» неоднозначно "
                        f"(id подразделений: {sorted(matches)})",
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
                        f"«{personnel_number}» уже существует",
                    )
                )
            elif personnel_number in seen_personnel_numbers:
                row_errors.append(
                    RowError(
                        row.row_number,
                        "personnel_number",
                        f"дублируется в файле (см. также строку "
                        f"{seen_personnel_numbers[personnel_number]})",
                    )
                )
            else:
                seen_personnel_numbers[personnel_number] = row.row_number

        if email is not None:
            email_lower = email.lower()
            if email_lower in existing["emails_lower"]:
                row_errors.append(
                    RowError(row.row_number, "email", f"«{email}» уже существует")
                )
            elif email_lower in seen_emails_lower:
                row_errors.append(
                    RowError(
                        row.row_number,
                        "email",
                        f"дублируется в файле (см. также строку {seen_emails_lower[email_lower]})",
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
            balances=balances,
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
            manager_target[pn] = ManagerTarget("file", ref)
        elif ref in existing["id_by_personnel_number"]:
            existing_id = existing["id_by_personnel_number"][ref]
            if existing["status_by_id"].get(existing_id) != "active":
                errors.append(
                    RowError(
                        candidate.row_number,
                        "manager_personnel_number",
                        f"«{ref}» ссылается на уволенного сотрудника",
                    )
                )
                del candidates[pn]
                continue
            manager_target[pn] = ManagerTarget("existing", existing_id)
        else:
            errors.append(
                RowError(
                    candidate.row_number,
                    "manager_personnel_number",
                    f"«{ref}» не найден ни среди существующих сотрудников, ни среди строк файла",
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
            next_pn = target.file_personnel_number if target is not None else None
            if next_pn is None:
                break
            pn = next_pn
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
                f"образует цикл manager_id со строками для табельных номеров "
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
            file_ref = target.file_personnel_number
            existing_ref = target.existing_employee_id
            if file_ref is not None and file_ref in rejected:
                rejected.add(pn)
                errors.append(
                    RowError(
                        candidate.row_number,
                        "manager_personnel_number",
                        f"«{file_ref}» отклонена, поэтому эта строка не может ссылаться на неё",
                    )
                )
                changed = True
            elif existing_ref is not None and not existing_healthy(existing_ref):
                rejected.add(pn)
                errors.append(
                    RowError(
                        candidate.row_number,
                        "manager_personnel_number",
                        f"цепочка подчинения через существующего сотрудника с id {existing_ref} "
                        "содержит цикл или превышает предельную глубину",
                    )
                )
                changed = True

    return rejected


async def _write_accepted(
    session: AsyncSession,
    accepted: list[_Candidate],
    manager_target: dict[str, _ManagerTarget],
    *,
    today: datetime.date,
    tracked_leave_types: dict[str, int],
    actor_account_id: int | None,
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
        employee = employees_by_pn[candidate.personnel_number]
        existing_ref = target.existing_employee_id
        employee.manager_id = (
            existing_ref
            if existing_ref is not None
            else employees_by_pn[target.file_personnel_number].id
        )

    session.add_all(
        Account(
            employee_id=employees_by_pn[candidate.personnel_number].id,
            auth_type="ldap",
            role="employee",
            is_active=True,
        )
        for candidate in accepted
    )

    # Начальные остатки - той же транзакцией (`FR-EMP-003/4`), только через функцию модуля
    # `balances`: журнала отсюда не касаются. Всё, чем она может отказать, проверено в фазе 1,
    # но не всё можно проверить заранее (вид могли сменить между фазами): отказ здесь не
    # отклоняет строку, а прерывает весь импорт (`import_employees`), ничего не записано.
    for candidate in accepted:
        for leave_type_code, amount in candidate.balances.items():
            await import_initial_balance(
                session,
                employee_id=employees_by_pn[candidate.personnel_number].id,
                leave_type_id=tracked_leave_types[leave_type_code],
                quantity=amount,
                actor_account_id=actor_account_id,
                entry_date=today,
            )

    await session.commit()


async def import_employees(
    session: AsyncSession,
    rows: list[ParsedRow],
    *,
    actor_account_id: int | None = None,
) -> ImportResult:
    """`actor_account_id` - учётная запись, от имени которой идёт импорт: автор событий
    «начальный остаток» (`FR-EMP-003/8`). Импорт без остатков автора не требует; строка с
    остатком при `None` прерывает импорт (`BalanceEntryError`, ничего не записано): молчаливого
    автора по умолчанию нет. Скрипта первичного развёртывания в репозитории НЕТ: контракт
    держит параметр, а скрипт передаст в него учётную запись первичного администратора."""
    for attempt in range(1, _MAX_WRITE_ATTEMPTS + 1):
        # PR #139 review, замечание C: на всю фазу 1+2 этой попытки, а не
        # только вокруг проверки графа, — снимок employment_status,
        # прочитанный `_load_existing`, должен оставаться свежим вплоть
        # до записи (см. докстринг модуля). Снимается COMMIT/ROLLBACK
        # текущей транзакции, поэтому на повторную попытку берётся заново.
        await lock_management_chain(session)
        existing = await _load_existing(session)
        departments_by_name = await _load_departments_by_name(session)
        today = await business_today(session)
        tracked_leave_types = await _load_tracked_leave_type_ids(session)

        candidates, errors = _validate_rows(
            rows,
            existing=existing,
            departments_by_name=departments_by_name,
            today=today,
            tracked_leave_types=tracked_leave_types,
        )
        manager_target = _resolve_manager_targets(candidates, existing, errors)
        cycle_members = _find_file_cycles(candidates, manager_target)
        rejected = _reject_broken_chains(
            candidates, manager_target, cycle_members, existing["manager_by_id"], errors
        )

        accepted = [candidate for pn, candidate in candidates.items() if pn not in rejected]

        try:
            if accepted:
                await _write_accepted(
                    session,
                    accepted,
                    manager_target,
                    today=today,
                    tracked_leave_types=tracked_leave_types,
                    actor_account_id=actor_account_id,
                )
        except BalanceEntryError:
            await session.rollback()
            raise
        except IntegrityError as exc:
            await session.rollback()
            field = unique_violation_field(exc)
            if field is None:
                # Не personnel_number/email — не гонка на известном
                # поле, а неожиданный сбой; не маскировать его повтором.
                raise
            if attempt == _MAX_WRITE_ATTEMPTS:
                raise ImportFileError(
                    f"Импорт не завершён: повторяющиеся конфликты записи "
                    f"по полю «{field}» после {attempt} попыток — "
                    "повторите загрузку"
                ) from exc
            continue  # повторить фазу 1+2 со свежим снимком БД

        accepted_row_numbers = {c.row_number for c in accepted}
        rejected_count = len(rows) - len(accepted_row_numbers)
        return ImportResult(
            accepted_count=len(accepted),
            rejected_count=rejected_count,
            errors=sorted(errors, key=lambda e: (e.row_number, e.field)),
            warning_rows=sorted(
                c.row_number
                for c in accepted
                if c.hire_date < today and _MAIN_LEAVE_TYPE_CODE not in c.balances
            ),
        )

    raise AssertionError("unreachable: loop above always returns or raises")
