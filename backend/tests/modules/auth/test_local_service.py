import datetime

import pytest

from app.core.security import hash_password
from app.modules.auth.exceptions import AccessDeniedError, AuthenticationError
from app.modules.auth.local_service import authenticate_local
from app.modules.auth.password_policy import DEFAULT_MAX_FAILED_ATTEMPTS
from tests.modules.auth.factories import make_account, make_employee

PASSWORD = "CorrectHorseBatteryStaple"


async def test_valid_credentials_via_employee_email_succeed(db_session) -> None:
    employee = await make_employee(db_session, email="ivanov@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
    )

    result = await authenticate_local(db_session, "ivanov@company.local", PASSWORD)

    assert result.access_token
    assert result.refresh_token
    assert result.role == "employee"


async def test_valid_credentials_via_service_login_succeed(db_session) -> None:
    await make_account(
        db_session,
        login="admin",
        auth_type="local",
        role="system_admin",
        password_hash=hash_password(PASSWORD),
        must_change_password=True,
    )

    result = await authenticate_local(db_session, "admin", PASSWORD)

    assert result.must_change_password is True


async def test_unknown_login_raises_authentication_error(db_session) -> None:
    with pytest.raises(AuthenticationError):
        await authenticate_local(db_session, "no-such-user@company.local", PASSWORD)


async def test_wrong_password_raises_authentication_error(db_session) -> None:
    employee = await make_employee(db_session, email="petrov@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
    )

    with pytest.raises(AuthenticationError):
        await authenticate_local(db_session, "petrov@company.local", "wrong-password")


async def test_ldap_only_account_cannot_authenticate_locally(db_session) -> None:
    employee = await make_employee(db_session, email="smirnova@company.local")
    await make_account(
        db_session, employee_id=employee.id, auth_type="ldap", role="employee"
    )

    with pytest.raises(AuthenticationError):
        await authenticate_local(db_session, "smirnova@company.local", PASSWORD)


async def test_repeated_failures_lock_the_account(db_session) -> None:
    employee = await make_employee(db_session, email="fedorova@company.local")
    account = await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
    )

    for _ in range(DEFAULT_MAX_FAILED_ATTEMPTS):
        with pytest.raises(AuthenticationError):
            await authenticate_local(db_session, "fedorova@company.local", "wrong-password")

    await db_session.refresh(account)
    assert account.failed_login_attempts == DEFAULT_MAX_FAILED_ATTEMPTS
    assert account.locked_until is not None

    # Даже правильный пароль не пускает, пока учётная запись заблокирована.
    with pytest.raises(AuthenticationError):
        await authenticate_local(db_session, "fedorova@company.local", PASSWORD)


async def test_successful_login_resets_failed_attempts(db_session) -> None:
    employee = await make_employee(db_session, email="sysadmin@company.local")
    account = await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
    )
    account.failed_login_attempts = DEFAULT_MAX_FAILED_ATTEMPTS - 1
    await db_session.flush()

    await authenticate_local(db_session, "sysadmin@company.local", PASSWORD)

    await db_session.refresh(account)
    assert account.failed_login_attempts == 0
    assert account.locked_until is None


async def test_disabled_account_denies_access_after_correct_password(db_session) -> None:
    employee = await make_employee(db_session, email="disabled@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
        is_active=False,
    )

    with pytest.raises(AccessDeniedError):
        await authenticate_local(db_session, "disabled@company.local", PASSWORD)


async def test_deactivated_employee_denies_access_after_correct_password(db_session) -> None:
    employee = await make_employee(
        db_session, email="terminated@company.local", employment_status="terminated"
    )
    await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
    )

    with pytest.raises(AccessDeniedError):
        await authenticate_local(db_session, "terminated@company.local", PASSWORD)


async def test_expired_lock_allows_login_again(db_session) -> None:
    employee = await make_employee(db_session, email="unlocked@company.local")
    account = await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
    )
    account.failed_login_attempts = DEFAULT_MAX_FAILED_ATTEMPTS
    account.locked_until = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)
    await db_session.flush()

    result = await authenticate_local(db_session, "unlocked@company.local", PASSWORD)

    assert result.access_token
