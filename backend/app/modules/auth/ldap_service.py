"""Сопоставление LDAP-пользователя с employees и выдача JWT (T-M1-07,
FR-AUTH-001)."""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt
from app.core.ldap_client import LdapAuthError, LdapSettingsData
from app.core.ldap_client import authenticate as ldap_bind_and_search
from app.modules.auth.access_control import enforce_access
from app.modules.auth.exceptions import (
    AccessDeniedError,
    AuthenticationError,
    LdapNotConfiguredError,
)
from app.modules.auth.repository import (
    get_account_by_employee_id,
    get_active_ldap_settings,
    get_employee_by_email,
)
from app.modules.auth.tokens import AuthResult, issue_tokens


async def authenticate_via_ldap(session: AsyncSession, login: str, password: str) -> AuthResult:
    ldap_settings = await get_active_ldap_settings(session)
    if ldap_settings is None:
        raise LdapNotConfiguredError(
            "LDAP is not configured; the system is running in embedded mode (FR-AUTH-003)"
        )

    params = LdapSettingsData(
        server_url=ldap_settings.server_url,
        port=ldap_settings.port,
        base_dn=ldap_settings.base_dn,
        bind_dn=ldap_settings.bind_dn,
        bind_password=decrypt(
            ldap_settings.bind_password_encrypted, get_settings().ldap_password_encryption_key
        ),
        user_match_attribute=ldap_settings.user_match_attribute,
    )

    try:
        matched_value = await asyncio.to_thread(ldap_bind_and_search, params, login, password)
    except LdapAuthError as exc:
        raise AuthenticationError("Invalid LDAP login or password") from exc

    employee = await get_employee_by_email(session, matched_value)
    if employee is None or employee.employment_status != "active":
        raise AccessDeniedError("No active employee record for this LDAP user")

    account = await get_account_by_employee_id(session, employee.id)
    if account is None:
        raise AccessDeniedError("No account provisioned for this employee")

    await enforce_access(session, account, employee=employee)
    return issue_tokens(account)
