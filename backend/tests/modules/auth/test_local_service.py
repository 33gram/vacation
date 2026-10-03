import datetime

import pytest

from app.core.security import hash_password
from app.modules.auth.exceptions import AccessDeniedError, AuthenticationError
from app.modules.auth.local_service import authenticate_local
from app.modules.auth.password_policy import DEFAULT_MAX_FAILED_ATTEMPTS, MAX_PASSWORD_BYTES
from tests.modules.auth.factories import make_account, make_employee

PASSWORD = "CorrectHorseBatteryStaple"


async def test_valid_credentials_via_employee_email_succeed(db_session) -> None:
    """`FR-AUTH-002/1`."""
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
    """`FR-AUTH-002/1` (служебная/административная учётная запись)."""
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


async def test_empty_password_is_rejected(db_session) -> None:
    employee = await make_employee(db_session, email="empty-pw@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
    )

    with pytest.raises(AuthenticationError):
        await authenticate_local(db_session, "empty-pw@company.local", "")


async def test_whitespace_only_password_is_rejected(db_session) -> None:
    employee = await make_employee(db_session, email="whitespace-pw@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
    )

    with pytest.raises(AuthenticationError):
        await authenticate_local(db_session, "whitespace-pw@company.local", "   ")


async def test_empty_password_is_rejected_even_for_unknown_login(db_session) -> None:
    """`FR-AUTH-004/1`."""
    # Проверка порядка: пустой пароль отклоняется независимо от того,
    # существует ли логин (FR-AUTH-004) — не только когда пароль в БД есть
    # с чем сравнить.
    with pytest.raises(AuthenticationError):
        await authenticate_local(db_session, "does-not-exist@company.local", "")


async def test_oversized_password_is_rejected_before_verifying_the_hash(
    db_session, monkeypatch
) -> None:
    # PR #140 review (второй круг), замечание 1: `MAX_PASSWORD_BYTES`
    # раньше проверялся только в `validate_password_strength` (задание/
    # смена пароля), которую вход не вызывает — `authenticate_local` шёл
    # прямо в `bcrypt.checkpw` (через `verify_password`). Патчим
    # `verify_password` так, чтобы падать, если его вообще вызвали:
    # обычный неверный пароль тоже поднял бы `AuthenticationError`, не
    # отличая этот случай от отклонения по длине — тест должен
    # доказывать именно «не дошло до сравнения хэшей», а не просто
    # «не совпало». См. также
    # `test_boundary_password_with_appended_tail_is_rejected` ниже —
    # закрываемая этой правкой живая коллизия.
    def _fail_if_called(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("verify_password must not run for an oversized password")

    monkeypatch.setattr("app.modules.auth.local_service.verify_password", _fail_if_called)

    employee = await make_employee(db_session, email="oversized-pw@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
    )
    oversized = "a" * (MAX_PASSWORD_BYTES + 1)

    with pytest.raises(AuthenticationError):
        await authenticate_local(db_session, "oversized-pw@company.local", oversized)


async def test_boundary_password_with_appended_tail_is_rejected(db_session) -> None:
    """`FR-AUTH-002/5`."""
    # PR #140 review (второй круг), замечание 1, живая коллизия: пароль
    # РОВНО на 72-байтовой границе легален и должен пройти как есть. До
    # этой правки тот же пароль с ЛЮБЫМ хвостом после байта 72 тоже
    # успешно аутентифицировал ту же учётную запись, потому что
    # `bcrypt.checkpw` усекает вход так же, как `bcrypt.hashpw` при
    # хэшировании — два разных по тексту пароля открывали один аккаунт.
    boundary_password = "Aa1!" * 18  # ровно 72 ASCII-байта
    assert len(boundary_password.encode("utf-8")) == MAX_PASSWORD_BYTES
    employee = await make_employee(db_session, email="boundary-pw@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(boundary_password),
    )

    result = await authenticate_local(db_session, "boundary-pw@company.local", boundary_password)
    assert result.access_token

    with pytest.raises(AuthenticationError):
        await authenticate_local(
            db_session, "boundary-pw@company.local", boundary_password + "EXTRA_TAIL"
        )


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
    """`FR-AUTH-002/2` (ограничение неуспешных попыток, временная
    блокировка)."""
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
    """`FR-AUTH-004/2` (учётная запись отключена)."""
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
    """`FR-AUTH-004/2` (карточка сотрудника деактивирована) - негативный
    сценарий, явно названный в задании `T-M1-23`."""
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
