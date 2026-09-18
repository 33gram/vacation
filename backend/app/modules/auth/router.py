"""`/api/auth/*` (T-M1-13)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.db.session import get_db_session
from app.models.account import Account
from app.modules.auth import ldap_service, local_service
from app.modules.auth.embedded_mode import is_embedded_mode
from app.modules.auth.password_policy import WeakPasswordError, validate_password_strength
from app.modules.auth.rbac import get_current_account
from app.modules.auth.tokens import AuthResult
from app.schemas.auth import LoginRequest, MeResponse, PasswordChangeRequest, TokenResponse

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
    """FR-AUTH-001/003: LDAP, если в `ldap_settings` есть активная запись;
    иначе — режим внедрения, вход тем же логином/паролем обслуживается
    локально, как того требует FR-AUTH-003."""
    if await is_embedded_mode(session):
        result = await local_service.authenticate_local(session, payload.login, payload.password)
    else:
        result = await ldap_service.authenticate_via_ldap(
            session, payload.login, payload.password
        )
    return _to_token_response(result)


@router.post("/login/local", response_model=TokenResponse)
async def login_local(
    payload: LoginRequest, session: AsyncSession = Depends(get_db_session)
) -> TokenResponse:
    """FR-AUTH-002: всегда локальный вход, независимо от состояния LDAP —
    служебные записи и сотрудники с явно выбранным локальным паролем."""
    result = await local_service.authenticate_local(session, payload.login, payload.password)
    return _to_token_response(result)


@router.post("/password/change", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
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
    if not verify_password(payload.current_password, account.local_password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid current password")

    try:
        validate_password_strength(payload.new_password)
    except WeakPasswordError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    account.local_password_hash = hash_password(payload.new_password)
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
