"""Конкурентно: импорт файла, назначающего существующего сотрудника
руководителем новой строки, и деактивация ЭТОГО ЖЕ сотрудника (PR #139
review, замечание C) — состояние «уволенный с активным подчинённым»
должно быть недостижимо независимо от того, какая сторона выигрывает.

`import_employees` теперь берёт `lock_management_chain` на всю фазу 1+2
(см. докстринг `import_service.py`) — ту же блокировку, что и
`deactivate_employee`. Простого одновременного старта (`asyncio.gather`)
достаточно для ПОЛОЖИТЕЛЬНОГО прогона: блокировка гарантирует
правильный результат при ЛЮБОМ порядке планирования, в отличие от
гонок без блокировки (`test_concurrent_org_changes.py`), где нужна
точная синхронизация, чтобы гонка вообще проявилась. Фикстура `client`
(НЕ `api_client`) — независимые соединения для обеих сторон.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.db.session import get_engine
from app.models.account import Account
from app.models.department import Department
from app.models.employee import Employee


async def _setup(marker: str) -> tuple[int, int, int, str]:
    """Возвращает (admin_account_id, department_id, manager_employee_id,
    manager_personnel_number)."""
    engine = get_engine()
    async with engine.connect() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        department = Department(name=f"Import Deact Dept {marker}")
        session.add(department)
        await session.flush()

        admin_employee = Employee(
            full_name="Import Deact Admin",
            personnel_number=f"IMPDEACT-ADMIN-{marker}",
            department_id=department.id,
            position="HR",
            hire_date=datetime.date(2024, 1, 1),
            email=f"import-deact-admin-{marker}@company.local",
            work_schedule_type="standard",
        )
        session.add(admin_employee)
        await session.flush()
        admin_account = Account(employee_id=admin_employee.id, auth_type="ldap", role="hr_admin")
        session.add(admin_account)

        manager_employee = Employee(
            full_name="Import Deact Manager",
            personnel_number=f"IMPDEACT-MGR-{marker}",
            department_id=department.id,
            position="Lead",
            hire_date=datetime.date(2024, 1, 1),
            email=f"import-deact-manager-{marker}@company.local",
            work_schedule_type="standard",
        )
        session.add(manager_employee)
        await session.flush()
        session.add(Account(employee_id=manager_employee.id, auth_type="ldap", role="employee"))

        await session.commit()
        admin_account_id = admin_account.id
        department_id = department.id
        manager_employee_id = manager_employee.id
        manager_personnel_number = manager_employee.personnel_number
        await session.close()

    return admin_account_id, department_id, manager_employee_id, manager_personnel_number


async def _cleanup(marker: str, department_id: int) -> None:
    engine = get_engine()
    async with engine.connect() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        rows = (
            await session.execute(
                Employee.__table__.select().where(
                    or_(
                        Employee.personnel_number == f"IMPDEACT-ADMIN-{marker}",
                        Employee.personnel_number == f"IMPDEACT-MGR-{marker}",
                        Employee.personnel_number == f"IMPDEACT-SUB-{marker}",
                    )
                )
            )
        ).all()
        ids = [row.id for row in rows]
        await session.execute(Account.__table__.delete().where(Account.employee_id.in_(ids)))
        await session.execute(Employee.__table__.delete().where(Employee.id.in_(ids)))
        await session.execute(Department.__table__.delete().where(Department.id == department_id))
        await session.commit()
        await session.close()


async def test_concurrent_import_assigning_manager_and_deactivating_them_never_leaves_bad_state(
    client,
) -> None:
    marker = uuid.uuid4().hex[:12]
    admin_account_id, department_id, manager_id, manager_personnel_number = await _setup(marker)
    department_name = f"Import Deact Dept {marker}"
    subordinate_personnel_number = f"IMPDEACT-SUB-{marker}"
    subordinate_email = f"import-deact-sub-{marker}@company.local"

    token = create_access_token(account_id=admin_account_id, role="hr_admin")
    headers = {"Authorization": f"Bearer {token}"}

    header = (
        "full_name,personnel_number,department,position,hire_date,email,"
        "work_schedule_type,manager_personnel_number,leave_category"
    )
    row = (
        f"Import Deact Sub,{subordinate_personnel_number},{department_name},Engineer,"
        f"2024-01-15,{subordinate_email},standard,{manager_personnel_number},"
    )
    content = f"{header}\n{row}\n".encode()

    try:
        responses = await asyncio.gather(
            client.post(
                "/api/employees/import",
                files={"file": ("import.csv", content, "text/csv")},
                headers=headers,
            ),
            client.post(
                f"/api/employees/{manager_id}/deactivate",
                json={"termination_date": "2024-06-01"},
                headers=headers,
            ),
        )

        for response in responses:
            assert response.status_code < 500, response.text

        engine = get_engine()
        async with engine.connect() as connection:
            session = AsyncSession(bind=connection, expire_on_commit=False)
            manager_row = await session.get(Employee, manager_id)
            subordinate_row = (
                await session.execute(
                    Employee.__table__.select().where(
                        Employee.personnel_number == subordinate_personnel_number
                    )
                )
            ).first()
            await session.close()

        if subordinate_row is not None:
            assert not (
                manager_row.employment_status == "terminated"
                and subordinate_row.manager_id == manager_id
                and subordinate_row.employment_status == "active"
            )
    finally:
        await _cleanup(marker, department_id)
