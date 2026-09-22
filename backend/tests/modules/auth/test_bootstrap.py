import pytest

from app.core.config import Settings, get_settings
from app.core.security import verify_password
from app.modules.auth.bootstrap import ensure_bootstrap_admin
from app.modules.auth.password_policy import MAX_PASSWORD_BYTES
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


async def test_raises_when_bootstrap_password_exceeds_byte_limit(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # PR #140 review (второй круг), замечание 3 — риск самой правки о
    # байтовой границе на входе: если `BOOTSTRAP_ADMIN_PASSWORD` длиннее
    # 72 байт, хэш строился бы по усечённому паролю, а вход ПОЛНЫМ
    # паролем отклонялся бы новой границей — администратор не смог бы
    # войти и не понял бы почему. Стартовать в таком случае нельзя.
    oversized_password = "a" * (MAX_PASSWORD_BYTES + 1)
    monkeypatch.setattr(
        "app.modules.auth.bootstrap.get_settings",
        lambda: Settings(_env_file=None, bootstrap_admin_password=oversized_password),
    )

    with pytest.raises(RuntimeError, match=str(MAX_PASSWORD_BYTES)):
        await ensure_bootstrap_admin(db_session)

    assert await count_active_system_admins(db_session) == 0


async def test_default_bootstrap_password_still_starts(db_session) -> None:
    # Отрицательный контроль к тесту выше: значение по умолчанию
    # ("changeme", далеко под границей) не задевается новой проверкой.
    settings = get_settings()
    assert len(settings.bootstrap_admin_password.encode("utf-8")) <= MAX_PASSWORD_BYTES

    await ensure_bootstrap_admin(db_session)

    account = await get_account_by_login(db_session, settings.bootstrap_admin_login)
    assert account is not None


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
