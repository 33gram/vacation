import pytest
from sqlalchemy.exc import IntegrityError

from app.models.account import Account
from app.models.department import Department
from tests.modules.auth.factories import make_employee


async def test_account_requires_exactly_one_of_employee_or_login(db_session) -> None:
    employee = await make_employee(db_session, email="both@company.local")
    db_session.add(
        Account(employee_id=employee.id, login="both", auth_type="local", role="employee")
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_account_requires_at_least_one_of_employee_or_login(db_session) -> None:
    db_session.add(Account(auth_type="local", role="employee"))

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_department_head_employee_fk_accepts_real_employee(db_session) -> None:
    head = await make_employee(db_session, email="head@company.local")
    department = Department(name="Тестовый отдел", head_employee_id=head.id)
    db_session.add(department)

    await db_session.flush()

    assert department.id is not None
