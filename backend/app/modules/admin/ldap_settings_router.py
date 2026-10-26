"""`/api/admin/ldap-settings*` (T-M1-16, FR-ADMIN-005) — доступ только
`system_admin`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.account import Account
from app.modules.admin import ldap_settings_service
from app.modules.auth.rbac import require_roles
from app.schemas.errors import AUTH_RESPONSES, error_responses
from app.schemas.ldap_settings import (
    LdapSettingsIn,
    LdapSettingsOut,
    LdapTestRequest,
    LdapTestResponse,
)

router = APIRouter(prefix="/api/admin", tags=["admin"], responses=AUTH_RESPONSES)

_require_system_admin = require_roles("system_admin")


@router.get("/ldap-settings", response_model=LdapSettingsOut | None)
async def get_ldap_settings(
    session: AsyncSession = Depends(get_db_session),
    _: Account = Depends(_require_system_admin),
) -> LdapSettingsOut | None:
    row = await ldap_settings_service.get_ldap_settings(session)
    return LdapSettingsOut.model_validate(row) if row is not None else None


@router.put("/ldap-settings", response_model=LdapSettingsOut)
async def put_ldap_settings(
    payload: LdapSettingsIn,
    session: AsyncSession = Depends(get_db_session),
    _: Account = Depends(_require_system_admin),
) -> LdapSettingsOut:
    row = await ldap_settings_service.upsert_ldap_settings(session, payload)
    return LdapSettingsOut.model_validate(row)


@router.post(
    "/ldap-settings/test", response_model=LdapTestResponse, responses=error_responses(400)
)
async def test_ldap_settings(
    payload: LdapTestRequest,
    session: AsyncSession = Depends(get_db_session),
    _: Account = Depends(_require_system_admin),
) -> LdapTestResponse:
    try:
        success, detail = await ldap_settings_service.test_ldap_connection(session, payload)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return LdapTestResponse(success=success, detail=detail)
