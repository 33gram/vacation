from app.core.config import get_settings
from app.core.security import verify_password
from app.modules.auth.bootstrap import ensure_bootstrap_admin
from app.modules.auth.repository import count_active_system_admins, get_account_by_login
from tests.modules.auth.factories import make_account, make_employee


async def test_creates_bootstrap_admin_when_none_exists(db_session) -> None:
    settings = get_settings()

    await ensure_bootstrap_admin(db_session)

    account = await get_account_by_login(db_session, settings.bootstrap_admin_login)
    assert account is not None
    assert account.role == "system_admin"
    assert account.auth_type == "local"
    assert account.employee_id is None
    assert account.must_change_password is True
    assert account.is_active is True
    assert verify_password(settings.bootstrap_admin_password, account.local_password_hash)


async def test_does_not_duplicate_when_called_twice(db_session) -> None:
    await ensure_bootstrap_admin(db_session)
    await ensure_bootstrap_admin(db_session)

    assert await count_active_system_admins(db_session) == 1


async def test_skips_when_active_system_admin_already_exists(db_session) -> None:
    employee = await make_employee(db_session, email="sysadmin2@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="ldap",
        role="system_admin",
        is_active=True,
    )

    await ensure_bootstrap_admin(db_session)

    settings = get_settings()
    bootstrap_account = await get_account_by_login(db_session, settings.bootstrap_admin_login)
    assert bootstrap_account is None


async def test_recreates_when_existing_system_admin_is_inactive(db_session) -> None:
    employee = await make_employee(db_session, email="sysadmin3@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="ldap",
        role="system_admin",
        is_active=False,
    )

    await ensure_bootstrap_admin(db_session)

    settings = get_settings()
    bootstrap_account = await get_account_by_login(db_session, settings.bootstrap_admin_login)
    assert bootstrap_account is not None


async def test_handles_login_collision_gracefully(db_session) -> None:
    settings = get_settings()
    # Симулирует гонку двух реплик при старте: запись с этим login уже
    # существует, хотя проверка count_active_system_admins её не видит
    # (не активна и не system_admin).
    await make_account(
        db_session,
        login=settings.bootstrap_admin_login,
        auth_type="local",
        role="employee",
        is_active=False,
    )

    await ensure_bootstrap_admin(db_session)  # не должно поднять исключение

    assert await count_active_system_admins(db_session) == 0
