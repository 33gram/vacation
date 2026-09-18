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
from app.core.ldap_client import (
    DEFAULT_LDAP_PORT,
    DEFAULT_USER_MATCH_ATTRIBUTE,
    LdapAuthError,
    LdapSettingsData,
)
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


def _settings_data_from_row(row: LdapSettings) -> LdapSettingsData:
    return LdapSettingsData(
        server_url=row.server_url,
        port=row.port,
        base_dn=row.base_dn,
        bind_dn=row.bind_dn,
        bind_password=decrypt(
            row.bind_password_encrypted, get_settings().ldap_password_encryption_key
        ),
        user_match_attribute=row.user_match_attribute,
    )


async def _run_ldap_test(params: LdapSettingsData) -> tuple[bool, str]:
    try:
        await asyncio.to_thread(ldap_test_connection, params)
    except LdapAuthError as exc:
        return False, str(exc)
    return True, "Соединение установлено"


async def _verify_saved_ldap_settings(
    session: AsyncSession, saved_row: LdapSettings
) -> tuple[bool, str]:
    """Пустое тело запроса (PR #137 review): проверяет именно сохранённую
    запись её собственным расшифрованным паролем и фиксирует исход в
    `last_tested_at`/`last_test_status` — это единственная проверка,
    которая выводит систему из режима внедрения (`is_embedded_mode`),
    поэтому в отличие от `_test_draft_ldap_params` она пишет в БД."""
    success, detail = await _run_ldap_test(_settings_data_from_row(saved_row))
    saved_row.last_tested_at = utcnow()
    saved_row.last_test_status = "success" if success else "failure"
    await session.commit()
    return success, detail


async def _test_draft_ldap_params(
    payload: LdapTestRequest, saved_row: LdapSettings | None
) -> tuple[bool, str]:
    """Хотя бы одно поле в теле запроса (PR #137 review): проверяет
    черновые/произвольные параметры — недостающие поля берутся из
    сохранённой записи, как в `upsert_ldap_settings`, чтобы можно было
    проверить только изменённое поле, не перепечатывая остальные. Ничего
    не сохраняет: успех здесь не подтверждает, что сохранённая
    конфигурация рабочая, — только что введённые значения годятся для
    сохранения.

    Недостающие поля читаются прямо из `saved_row` (PR #137 review, круг
    3, замечание #3): пароль расшифровывается только когда тело запроса
    его не содержит и фолбэк на сохранённый действительно нужен — не
    имеет смысла держать открытый пароль в памяти, если он и не
    понадобится."""

    def pick(value: str | None, attr: str) -> str | None:
        if value is not None:
            return value
        return getattr(saved_row, attr) if saved_row is not None else None

    server_url = pick(payload.server_url, "server_url")
    base_dn = pick(payload.base_dn, "base_dn")
    bind_dn = pick(payload.bind_dn, "bind_dn")

    if payload.bind_password is not None:
        bind_password: str | None = payload.bind_password.get_secret_value()
    elif saved_row is not None:
        bind_password = decrypt(
            saved_row.bind_password_encrypted, get_settings().ldap_password_encryption_key
        )
    else:
        bind_password = None

    if server_url is None or base_dn is None or bind_dn is None or bind_password is None:
        raise ValueError("Укажите все параметры подключения для проверки")

    port = payload.port if payload.port is not None else (saved_row.port if saved_row else None)
    user_match_attribute = pick(payload.user_match_attribute, "user_match_attribute")
    if user_match_attribute is None:
        user_match_attribute = DEFAULT_USER_MATCH_ATTRIBUTE
    params = LdapSettingsData(
        server_url=server_url,
        port=port if port is not None else DEFAULT_LDAP_PORT,
        base_dn=base_dn,
        bind_dn=bind_dn,
        bind_password=bind_password,
        user_match_attribute=user_match_attribute,
    )
    return await _run_ldap_test(params)


async def test_ldap_connection(session: AsyncSession, payload: LdapTestRequest) -> tuple[bool, str]:
    """`POST /ldap-settings/test` (FR-AUTH-003/FR-ADMIN-005): выбирает
    между `_verify_saved_ldap_settings` и `_test_draft_ldap_params` по
    тому, задано ли в запросе хоть одно поле — см. докстринги обеих."""
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
        return await _verify_saved_ldap_settings(session, saved_row)

    return await _test_draft_ldap_params(payload, saved_row)
