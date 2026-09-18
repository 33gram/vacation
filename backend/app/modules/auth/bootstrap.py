"""Bootstrap-учётная запись администратора при первом запуске (T-M1-29,
FR-AUTH-005)."""

from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import hash_password
from app.models.account import Account
from app.modules.auth.repository import count_active_system_admins

logger = logging.getLogger(__name__)


async def ensure_bootstrap_admin(session: AsyncSession) -> None:
    """Создаёт локальную учётную запись `system_admin` из
    `BOOTSTRAP_ADMIN_LOGIN`/`BOOTSTRAP_ADMIN_PASSWORD`, если в `accounts`
    нет ни одной активной записи с этой ролью. Идемпотентно — безопасно
    вызывать при каждом старте приложения (в т.ч. с нескольких реплик,
    NFR-SCALE-001: конкурентная вставка отклоняется уникальностью `login`).

    Пароль из `BOOTSTRAP_ADMIN_PASSWORD` не проверяется парольной
    политикой (T-M1-25): `must_change_password=True` форсирует его
    немедленную смену при первом входе, а значение по умолчанию
    ("changeme") должно оставаться пригодным для запуска "из коробки"
    в dev-окружении.
    """
    if await count_active_system_admins(session) > 0:
        return

    settings = get_settings()
    account = Account(
        employee_id=None,
        login=settings.bootstrap_admin_login,
        auth_type="local",
        local_password_hash=hash_password(settings.bootstrap_admin_password),
        must_change_password=True,
        role="system_admin",
        is_active=True,
    )

    try:
        session.add(account)
        await session.commit()
    except IntegrityError:
        # Гонка с другой репликой при старте — запись уже создана, это не ошибка.
        await session.rollback()
        return

    logger.info(
        "Created bootstrap system_admin account",
        extra={"login": settings.bootstrap_admin_login},
    )
