"""Управление параметрами подключения к LDAP (T-M1-16, FR-ADMIN-005).

`ldap_settings` — singleton-таблица: в системе одна корпоративная
директория, поэтому берётся первая (и единственная) строка.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.ldap_client import LdapAuthError, LdapSettingsData
from app.core.ldap_client import test_connection as ldap_test_connection
from app.models.ldap_settings import LdapSettings
from app.schemas.ldap_settings import LdapSettingsIn, LdapTestRequest


async def get_ldap_settings(session: AsyncSession) -> LdapSettings | None:
    result = await session.execute(select(LdapSettings).limit(1))
    return result.scalars().first()


async def upsert_ldap_settings(session: AsyncSession, payload: LdapSettingsIn) -> LdapSettings:
    """Сохраняет параметры как переданы, без обязательной проверки
    соединения (решение по FR-ADMIN-005 — см. тело коммита T-M1-16).
    PUT без `bind_password` не трогает уже сохранённый пароль."""
    settings_row = await get_ldap_settings(session)
    if settings_row is None:
        settings_row = LdapSettings(bind_password_encrypted="")
        session.add(settings_row)

    settings_row.server_url = payload.server_url
    settings_row.port = payload.port
    settings_row.base_dn = payload.base_dn
    settings_row.bind_dn = payload.bind_dn
    settings_row.user_match_attribute = payload.user_match_attribute
    settings_row.is_active = payload.is_active

    if payload.bind_password:
        settings_row.bind_password_encrypted = encrypt(
            payload.bind_password, key=get_settings().ldap_password_encryption_key
        )

    await session.commit()
    await session.refresh(settings_row)
    return settings_row


async def test_ldap_connection(payload: LdapTestRequest) -> tuple[bool, str]:
    """`POST /ldap-settings/test`: проверяет соединение по переданным
    параметрам напрямую, ничего не сохраняя."""
    params = LdapSettingsData(
        server_url=payload.server_url,
        port=payload.port,
        base_dn=payload.base_dn,
        bind_dn=payload.bind_dn,
        bind_password=payload.bind_password,
        user_match_attribute=payload.user_match_attribute,
    )
    try:
        await asyncio.to_thread(ldap_test_connection, params)
    except LdapAuthError as exc:
        return False, str(exc)
    return True, "Соединение установлено"
