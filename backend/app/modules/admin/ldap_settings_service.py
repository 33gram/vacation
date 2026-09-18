"""Управление параметрами подключения к LDAP (T-M1-16, FR-ADMIN-005).

`ldap_settings` — singleton-таблица: в системе одна корпоративная
директория, поэтому берётся первая (и единственная) строка.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt, encrypt
from app.core.ldap_client import LdapAuthError, LdapSettingsData
from app.core.ldap_client import test_connection as ldap_test_connection
from app.core.time_utils import utcnow
from app.models.ldap_settings import LdapSettings
from app.schemas.ldap_settings import LdapSettingsIn, LdapTestRequest


async def get_ldap_settings(session: AsyncSession) -> LdapSettings | None:
    result = await session.execute(select(LdapSettings).limit(1))
    return result.scalars().first()


async def upsert_ldap_settings(session: AsyncSession, payload: LdapSettingsIn) -> LdapSettings:
    """Сохраняет параметры как переданы, без обязательной проверки
    соединения (решение по FR-ADMIN-005 — см. тело коммита T-M1-16).
    PUT без `bind_password` не трогает уже сохранённый пароль.

    Каждое сохранение сбрасывает `last_tested_at`/`last_test_status`
    (PR #137 review): иначе устаревший `'success'` от прошлой конфигурации
    продолжал бы выводить систему из режима внедрения (`is_embedded_mode`)
    для параметров, которые с тех пор поменяли и ни разу не проверяли —
    именно та несостыковка, которую отмечает FR-AUTH-003."""
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
    settings_row.last_tested_at = None
    settings_row.last_test_status = None

    if payload.bind_password:
        settings_row.bind_password_encrypted = encrypt(
            payload.bind_password, key=get_settings().ldap_password_encryption_key
        )

    await session.commit()
    await session.refresh(settings_row)
    return settings_row


async def test_ldap_connection(session: AsyncSession, payload: LdapTestRequest) -> tuple[bool, str]:
    """`POST /ldap-settings/test` (PR #137 review, FR-AUTH-003/FR-ADMIN-005).

    Пустое тело (`payload` без единого поля) проверяет уже сохранённую
    запись — своим собственным расшифрованным паролем — и фиксирует исход
    в `last_tested_at`/`last_test_status`, поскольку именно эта проверка
    выводит систему из режима внедрения (`is_embedded_mode`).

    Любое переданное поле означает проверку черновых/произвольных
    параметров (недостающие поля берутся из сохранённой записи, как в
    `upsert_ldap_settings`) — такой вызов ничего не сохраняет: он не
    подтверждает, что сохранённая конфигурация рабочая, а только помогает
    администратору проверить значения перед сохранением."""
    saved_row = await get_ldap_settings(session)
    is_verifying_saved = all(
        value is None
        for value in (
            payload.server_url,
            payload.port,
            payload.base_dn,
            payload.bind_dn,
            payload.bind_password,
            payload.user_match_attribute,
        )
    )

    if is_verifying_saved:
        if saved_row is None:
            raise ValueError("Параметры LDAP ещё не сохранены — нечего проверять")
        params = LdapSettingsData(
            server_url=saved_row.server_url,
            port=saved_row.port,
            base_dn=saved_row.base_dn,
            bind_dn=saved_row.bind_dn,
            bind_password=decrypt(
                saved_row.bind_password_encrypted, get_settings().ldap_password_encryption_key
            ),
            user_match_attribute=saved_row.user_match_attribute,
        )
    else:
        server_url = payload.server_url if payload.server_url is not None else (
            saved_row.server_url if saved_row else None
        )
        base_dn = payload.base_dn if payload.base_dn is not None else (
            saved_row.base_dn if saved_row else None
        )
        bind_dn = payload.bind_dn if payload.bind_dn is not None else (
            saved_row.bind_dn if saved_row else None
        )
        bind_password = (
            payload.bind_password.get_secret_value()
            if payload.bind_password is not None
            else (
                decrypt(
                    saved_row.bind_password_encrypted, get_settings().ldap_password_encryption_key
                )
                if saved_row
                else None
            )
        )
        if server_url is None or base_dn is None or bind_dn is None or bind_password is None:
            raise ValueError("Укажите все параметры подключения для проверки")
        port = payload.port if payload.port is not None else (saved_row.port if saved_row else 389)
        params = LdapSettingsData(
            server_url=server_url,
            port=port,
            base_dn=base_dn,
            bind_dn=bind_dn,
            bind_password=bind_password,
            user_match_attribute=(
                payload.user_match_attribute
                if payload.user_match_attribute is not None
                else (saved_row.user_match_attribute if saved_row else "mail")
            ),
        )

    try:
        await asyncio.to_thread(ldap_test_connection, params)
        success, detail = True, "Соединение установлено"
    except LdapAuthError as exc:
        success, detail = False, str(exc)

    if is_verifying_saved:
        assert saved_row is not None  # для mypy: проверено выше
        saved_row.last_tested_at = utcnow()
        saved_row.last_test_status = "success" if success else "failure"
        await session.commit()

    return success, detail
