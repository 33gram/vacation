"""Режим внедрения — работа без подключённого LDAP (T-M1-26, FR-AUTH-003).

Админский API управления `ldap_settings` (`T-M1-16`) — отдельная ветка;
здесь реализована только сервисная логика: определение режима и отказ
LDAP-входа с понятной причиной, пока каталог не настроен.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.repository import get_active_ldap_settings


async def is_embedded_mode(session: AsyncSession) -> bool:
    return await get_active_ldap_settings(session) is None
