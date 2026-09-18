"""Режим внедрения — работа без подключённого LDAP (T-M1-26, FR-AUTH-003).

FR-AUTH-003 переключает способ входа по умолчанию на LDAP, WHEN
администратор «сохраняет И УСПЕШНО ПРОВЕРЯЕТ» параметры — то есть
активной записи `is_active=true` недостаточно: пока
`POST /admin/ldap-settings/test` ни разу не подтвердил именно сохранённые
параметры, `last_test_status` остаётся не `'success'` (включая `NULL`
сразу после сохранения) и режим внедрения не завершается.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.repository import get_active_ldap_settings


async def is_embedded_mode(session: AsyncSession) -> bool:
    settings_row = await get_active_ldap_settings(session)
    return settings_row is None or settings_row.last_test_status != "success"
