"""`import_service.import_employees` (T-M1-11, FR-EMP-003) — двухфазная
валидация и запись, включая проверку графа `manager_id` на циклы (PR
#138)."""

from __future__ import annotations

import itertools

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.account import Account
from app.models.employee import Employee
from app.modules.employees import import_service
from app.modules.employees.import_parsing import ImportFileError, ParsedRow
from app.modules.employees.import_service import import_employees
from tests.fake_integrity_error import fake_integrity_error
from tests.modules.auth.factories import make_department, make_employee

_seq = itertools.count(1)


def _row(row_number: int, **overrides: str | None) -> ParsedRow:
    n = next(_seq)
    values: dict[str, str | None] = {
        "full_name": f"Employee {n}",
        "personnel_number": f"IMP-{n}",
        "department": "IT",
        "position": "Инженер",
        "hire_date": "2024-01-15",
        "email": f"imp-{n}@company.local",
        "work_schedule_type": "standard",
        "manager_personnel_number": None,
        "leave_category": None,
    }
    values.update(overrides)
    return ParsedRow(row_number=row_number, values=values)


async def _employee_by_pn(db_session, personnel_number: str) -> Employee | None:
    return (
        await db_session.execute(
            select(Employee).where(Employee.personnel_number == personnel_number)
        )
    ).scalar_one_or_none()


async def test_import_accepts_valid_rows(db_session) -> None:
    await make_department(db_session, name="IT")

    rows = [
        _row(2, personnel_number="MGR-1", full_name="Manager"),
        _row(
            3,
            personnel_number="SUB-1",
            full_name="Subordinate",
            manager_personnel_number="MGR-1",
        ),
    ]

    result = await import_employees(db_session, rows)

    assert result.accepted_count == 2
    assert result.rejected_count == 0
    assert result.errors == []

    manager = await _employee_by_pn(db_session, "MGR-1")
    subordinate = await _employee_by_pn(db_session, "SUB-1")
    assert subordinate.manager_id == manager.id

    accounts = (
        await db_session.execute(
            select(Account).where(Account.employee_id.in_([manager.id, subordinate.id]))
        )
    ).scalars().all()
    assert len(accounts) == 2
    assert {a.auth_type for a in accounts} == {"ldap"}
    assert {a.role for a in accounts} == {"employee"}
    assert all(a.is_active for a in accounts)


async def test_import_resolves_manager_listed_below_subordinate_in_file(db_session) -> None:
    # Решение plan.md §6: связи разрешаются ПОСЛЕ чтения всего файла —
    # руководитель может стоять НИЖЕ подчинённого в файле.
    await make_department(db_session, name="IT")

    rows = [
        _row(
            2,
            personnel_number="SUB-2",
            full_name="Subordinate",
            manager_personnel_number="MGR-2",
        ),
        _row(3, personnel_number="MGR-2", full_name="Manager"),
    ]

    result = await import_employees(db_session, rows)

    assert result.accepted_count == 2
    assert result.rejected_count == 0

    manager = await _employee_by_pn(db_session, "MGR-2")
    subordinate = await _employee_by_pn(db_session, "SUB-2")
    assert subordinate.manager_id == manager.id


async def test_import_rejects_file_cycle_and_accepts_unrelated_row(db_session) -> None:
    await make_department(db_session, name="IT")

    rows = [
        _row(2, personnel_number="A", manager_personnel_number="C"),
        _row(3, personnel_number="B", manager_personnel_number="A"),
        _row(4, personnel_number="C", manager_personnel_number="B"),
        _row(5, personnel_number="D"),  # не участвует в цикле
    ]

    result = await import_employees(db_session, rows)

    assert result.accepted_count == 1
    assert result.rejected_count == 3
    cycle_error_rows = {
        e.row_number for e in result.errors if e.field == "manager_personnel_number"
    }
    assert cycle_error_rows == {2, 3, 4}

    for pn in ("A", "B", "C"):
        assert await _employee_by_pn(db_session, pn) is None
    assert await _employee_by_pn(db_session, "D") is not None


async def test_import_cascades_rejection_to_row_referencing_a_cycle_member(db_session) -> None:
    await make_department(db_session, name="IT")

    rows = [
        _row(2, personnel_number="A", manager_personnel_number="B"),
        _row(3, personnel_number="B", manager_personnel_number="A"),
        # E ссылается на участника цикла (A) — E тоже должна быть
        # отклонена: её manager_id иначе указывал бы на никогда не
        # записанную строку.
        _row(4, personnel_number="E", manager_personnel_number="A"),
    ]

    result = await import_employees(db_session, rows)

    assert result.accepted_count == 0
    assert result.rejected_count == 3
    for pn in ("A", "B", "E"):
        assert await _employee_by_pn(db_session, pn) is None


async def test_import_rejects_row_closing_into_pre_existing_corrupted_chain(db_session) -> None:
    """Строка, чей руководитель ведёт в УЖЕ существующий, испорченный
    (циклический) граф `manager_id`. Импорт только создаёт строки и
    никогда не правит существующие — поэтому НОВЫЙ цикл, замкнутый
    целиком через уже существующего сотрудника, структурно невозможен
    (ни одна существующая запись не может получить ребро назад к строке
    файла). Возможный и проверяемый сценарий — обратный: строка ссылается
    на существующего сотрудника, чей СОБСТВЕННЫЙ граф уже испорчен (см.
    tasks.md, T-M1-11 — «цикл в manager_id может попасть в базу мимо
    проверок API»), и это не должно тихо пройти импорт дальше."""
    await make_department(db_session, name="IT")
    existing_a = await make_employee(db_session, email="existing-a@company.local")
    existing_b = await make_employee(
        db_session, email="existing-b@company.local", manager_id=existing_a.id
    )
    # Ту же пару, которую в норме API никогда не допустит (PR #138),
    # замыкаем напрямую через ORM — единственный способ смоделировать
    # уже испорченные данные для этого теста.
    existing_a.manager_id = existing_b.id
    await db_session.flush()

    rows = [_row(2, personnel_number="F", manager_personnel_number=existing_a.personnel_number)]

    result = await import_employees(db_session, rows)

    assert result.accepted_count == 0
    assert result.rejected_count == 1
    assert result.errors[0].field == "manager_personnel_number"
    # Устойчивый признак — id существующего сотрудника, чья цепочка
    # испорчена, а не формулировка сообщения (T-M1-47: теперь по-русски).
    assert str(existing_a.id) in result.errors[0].reason
    assert await _employee_by_pn(db_session, "F") is None


async def test_import_partial_success_lists_reasons_and_correct_summary(db_session) -> None:
    await make_department(db_session, name="IT")

    rows = [
        _row(2, personnel_number="OK-1"),
        _row(3, full_name=None),  # обязательное поле пусто
        _row(4, hire_date="not-a-date"),
    ]

    result = await import_employees(db_session, rows)

    assert result.accepted_count == 1
    assert result.rejected_count == 2
    reasons_by_row = {e.row_number: e.field for e in result.errors}
    assert reasons_by_row[3] == "full_name"
    assert reasons_by_row[4] == "hire_date"
    assert await _employee_by_pn(db_session, "OK-1") is not None


async def test_import_rejects_personnel_number_already_in_db_without_updating(db_session) -> None:
    department = await make_department(db_session, name="IT")
    existing = await make_employee(
        db_session,
        email="existing-pn@company.local",
        department_id=department.id,
        full_name="Original Name",
    )

    rows = [_row(2, personnel_number=existing.personnel_number, full_name="Attempted Overwrite")]

    result = await import_employees(db_session, rows)

    assert result.accepted_count == 0
    assert result.rejected_count == 1
    assert result.errors[0].field == "personnel_number"
    # Устойчивый признак — сам табельный номер, а не формулировка
    # сообщения (T-M1-47: теперь по-русски).
    assert existing.personnel_number in result.errors[0].reason

    row = await _employee_by_pn(db_session, existing.personnel_number)
    assert row.full_name == "Original Name"


async def test_import_rejects_duplicate_personnel_number_within_file(db_session) -> None:
    await make_department(db_session, name="IT")

    rows = [
        _row(2, personnel_number="DUP-1", full_name="First"),
        _row(3, personnel_number="DUP-1", full_name="Second"),
    ]

    result = await import_employees(db_session, rows)

    assert result.accepted_count == 1
    assert result.rejected_count == 1
    assert result.errors[0].row_number == 3
    assert result.errors[0].field == "personnel_number"
    row = await _employee_by_pn(db_session, "DUP-1")
    assert row.full_name == "First"


async def test_import_rejects_ambiguous_department_name(db_session) -> None:
    parent_a = await make_department(db_session, name="Branch A")
    parent_b = await make_department(db_session, name="Branch B")
    await make_department(db_session, name="Sales", parent_id=parent_a.id)
    await make_department(db_session, name="Sales", parent_id=parent_b.id)

    rows = [_row(2, department="Sales")]

    result = await import_employees(db_session, rows)

    assert result.accepted_count == 0
    assert result.errors[0].field == "department"
    # Устойчивый признак — само название подразделения, а не
    # формулировка сообщения (T-M1-47: теперь по-русски).
    assert "Sales" in result.errors[0].reason


async def test_import_rejects_manager_reference_not_found_anywhere(db_session) -> None:
    await make_department(db_session, name="IT")

    rows = [_row(2, manager_personnel_number="NO-SUCH-PERSON")]

    result = await import_employees(db_session, rows)

    assert result.accepted_count == 0
    assert result.errors[0].field == "manager_personnel_number"
    # Устойчивый признак — сама (несуществующая) ссылка, а не
    # формулировка сообщения (T-M1-47: теперь по-русски).
    assert "NO-SUCH-PERSON" in result.errors[0].reason


async def test_import_rejects_manager_reference_to_terminated_existing_employee(db_session) -> None:
    department = await make_department(db_session, name="IT")
    terminated = await make_employee(
        db_session,
        email="terminated-mgr@company.local",
        department_id=department.id,
        employment_status="terminated",
    )

    rows = [_row(2, manager_personnel_number=terminated.personnel_number)]

    result = await import_employees(db_session, rows)

    assert result.accepted_count == 0
    assert result.errors[0].field == "manager_personnel_number"
    # Устойчивый признак — табельный номер уволенного, а не
    # формулировка сообщения (T-M1-47: теперь по-русски).
    assert terminated.personnel_number in result.errors[0].reason


async def test_import_reraises_unexpected_integrity_error_without_retry(
    db_session, monkeypatch
) -> None:
    await make_department(db_session, name="IT")
    rows = [_row(2)]
    call_count = {"n": 0}

    async def _fake_write_accepted(session, accepted, manager_target):
        call_count["n"] += 1
        raise fake_integrity_error("some_unrelated_constraint")

    monkeypatch.setattr(import_service, "_write_accepted", _fake_write_accepted)

    with pytest.raises(IntegrityError):
        await import_employees(db_session, rows)

    assert call_count["n"] == 1


async def test_import_raises_import_file_error_after_exhausting_retries(
    db_session, monkeypatch
) -> None:
    await make_department(db_session, name="IT")
    # Commit releases the savepoint the department was created under, so
    # the retry loop's own `session.rollback()` calls below (triggered by
    # the fake write failure) can't undo this arrange-time setup along
    # the way — otherwise the second attempt would reject the row for a
    # missing department instead of retrying the write.
    await db_session.commit()
    rows = [_row(2)]
    call_count = {"n": 0}

    async def _fake_write_accepted(session, accepted, manager_target):
        call_count["n"] += 1
        raise fake_integrity_error("employees_personnel_number_key")

    monkeypatch.setattr(import_service, "_write_accepted", _fake_write_accepted)

    with pytest.raises(ImportFileError):
        await import_employees(db_session, rows)

    assert call_count["n"] == import_service._MAX_WRITE_ATTEMPTS
