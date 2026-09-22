"""`/api/auth/*` (T-M1-13)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.db.session import get_db_session
from app.models.account import Account
from app.models.employee import Employee
from app.modules.auth import ldap_service, local_service
from app.modules.auth.embedded_mode import is_embedded_mode
from app.modules.auth.password_policy import WeakPasswordError, validate_password_strength
from app.modules.auth.rbac import get_current_account
from app.modules.auth.tokens import AuthResult
from app.schemas.auth import LoginRequest, MeResponse, PasswordChangeRequest, TokenResponse
from app.schemas.errors import ErrorDetail

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _to_token_response(result: AuthResult) -> TokenResponse:
    return TokenResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        role=result.role,
        must_change_password=result.must_change_password,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest, session: AsyncSession = Depends(get_db_session)
) -> TokenResponse:
    """FR-AUTH-001/003: LDAP, если в `ldap_settings` есть активная и
    подтверждённая запись; иначе — режим внедрения, вход тем же
    логином/паролем обслуживается локально (FR-AUTH-003).

    Сотрудники с явно выбранным локальным паролем (`accounts.auth_type =
    'local'`) продолжают входить локально и при настроенном LDAP —
    диспетчеризация смотрит на способ входа из карточки, а не только на
    режим внедрения (T-M1-32)."""
    if await is_embedded_mode(session) or await local_service.uses_local_auth(
        session, payload.login
    ):
        result = await local_service.authenticate_local(
            session, payload.login, payload.password.get_secret_value()
        )
    else:
        result = await ldap_service.authenticate_via_ldap(
            session, payload.login, payload.password.get_secret_value()
        )
    return _to_token_response(result)


@router.post("/login/local", response_model=TokenResponse)
async def login_local(
    payload: LoginRequest, session: AsyncSession = Depends(get_db_session)
) -> TokenResponse:
    """FR-AUTH-002: всегда локальный вход, независимо от состояния LDAP —
    служебные записи и сотрудники с явно выбранным локальным паролем."""
    result = await local_service.authenticate_local(
        session, payload.login, payload.password.get_secret_value()
    )
    return _to_token_response(result)


async def _identity_context(
    session: AsyncSession, account: Account
) -> dict[str, str | None]:
    """Контекст для `validate_password_strength` берётся у ТЕКУЩЕЙ
    учётной записи (T-M1-38). Ровно одно из `employee_id`/`login`
    задано (`ck_accounts_employee_or_login`): служебная запись без
    карточки сотрудника не имеет ни e-mail, ни ФИО, и правило про них
    для неё просто не проверяется — явно, веткой ниже, а не потому что
    `Employee` окажется `None` и исключение случайно не поднимется."""
    if account.employee_id is None:
        return {"login": account.login, "email": None, "full_name": None}
    employee = await session.get(Employee, account.employee_id)
    assert employee is not None, "accounts.employee_id без карточки — нарушен инвариант"
    return {"login": None, "email": employee.email, "full_name": employee.full_name}


@router.post(
    "/password/change",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    responses={400: {"model": ErrorDetail}},
)
async def change_password(
    payload: PasswordChangeRequest,
    account: Account = Depends(get_current_account),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Доступен и при `must_change_password=True` (не идёт через
    `require_roles`) — иначе первичный пароль было бы нечем сменить."""
    if account.auth_type != "local" or account.local_password_hash is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only local accounts have a password to change",
        )
    # PR #140 review (третий круг, замечание 2в): `current_password`
    # намеренно НЕ ограничен `MAX_PASSWORD_BYTES`, в отличие от входа
    # (`local_service.authenticate_local`) и `new_password` ниже.
    # Отличие оправдано: вызывающий уже аутентифицирован (`Depends(
    # get_current_account)`), а `account.local_password_hash` по
    # построению никогда не хэширован из пароля длиннее 72 байт — все
    # четыре места, где приложение вызывает `hash_password` (здесь,
    # `bootstrap.py`, `employee_service.py` × 2), проверяют это ДО
    # хэширования. Усечение bcrypt при сверке здесь не может дать
    # совпадения, которого не было бы и без усечения: хранимому хэшу
    # соответствуют только первые ≤72 байта, а больше 72 байт от
    # `current_password` bcrypt и не увидит.
    if not verify_password(
        payload.current_password.get_secret_value(), account.local_password_hash
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid current password")

    try:
        validate_password_strength(
            payload.new_password.get_secret_value(), **await _identity_context(session, account)
        )
    except WeakPasswordError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.violations) from exc

    account.local_password_hash = hash_password(payload.new_password.get_secret_value())
    account.must_change_password = False
    await session.commit()


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def logout(_: Account = Depends(get_current_account)) -> None:
    """См. обоснование выбора (stateless JWT, не отзывается) в теле
    коммита T-M1-13: клиент обязан удалить оба токена локально."""
    return None


@router.get("/me", response_model=MeResponse)
async def me(
    account: Account = Depends(get_current_account),
    session: AsyncSession = Depends(get_db_session),
) -> MeResponse:
    return MeResponse(
        id=account.id,
        role=account.role,
        is_active=account.is_active,
        must_change_password=account.must_change_password,
        employee_id=account.employee_id,
        login=account.login,
        embedded_mode=await is_embedded_mode(session),
    )
