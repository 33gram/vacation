"""Bootstrap-учётная запись администратора при первом запуске (T-M1-29,
FR-AUTH-005)."""

from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import hash_password
from app.models.account import Account
from app.modules.auth.password_policy import MAX_PASSWORD_BYTES
from app.modules.auth.repository import count_active_system_admins

logger = logging.getLogger(__name__)


def _reject_oversized_bootstrap_password(password: str) -> None:
    """FR-AUTH-005 (раздел 13, п. 20): если `BOOTSTRAP_ADMIN_PASSWORD`
    длиннее `MAX_PASSWORD_BYTES` в UTF-8, `hash_password` ниже создал бы
    хэш по молча усечённому паролю, а вход ПОЛНЫМ паролем отклонялся бы
    новой границей длины на входе (`local_service.authenticate_local`,
    тот же п. 20) — администратор не смог бы войти и не понял бы,
    почему. Падаем на старте с понятным сообщением, а не создаём
    учётную запись, в которую нельзя будет войти."""
    password_bytes = len(password.encode("utf-8"))
    if password_bytes > MAX_PASSWORD_BYTES:
        raise RuntimeError(
            f"BOOTSTRAP_ADMIN_PASSWORD is {password_bytes} bytes when UTF-8 encoded, "
            f"over the {MAX_PASSWORD_BYTES}-byte limit bcrypt hashes and the login "
            "endpoint now enforces (PR #140 review): the account would be created "
            "with a hash of only its first "
            f"{MAX_PASSWORD_BYTES} bytes, and logging in with the full password "
            "would then be rejected. Set a shorter BOOTSTRAP_ADMIN_PASSWORD."
        )


async def ensure_bootstrap_admin(session: AsyncSession) -> None:
    """Создаёт локальную учётную запись `system_admin` из
    `BOOTSTRAP_ADMIN_LOGIN`/`BOOTSTRAP_ADMIN_PASSWORD`, если в `accounts`
    нет ни одной активной записи с этой ролью. Идемпотентно — безопасно
    вызывать при каждом старте приложения (в т.ч. с нескольких реплик,
    NFR-SCALE-001: конкурентная вставка отклоняется уникальностью `login`).

    Пароль из `BOOTSTRAP_ADMIN_PASSWORD` НЕ проверяется парольной
    политикой (T-M1-25): `must_change_password=True` форсирует его
    немедленную смену при первом входе, а значение по умолчанию
    ("changeme") должно оставаться пригодным для запуска "из коробки"
    в dev-окружении. Исключение — длина (PR #140 review, второй круг,
    FR-AUTH-005): она проверяется, потому что от неё зависит, сможет ли
    администратор вообще войти — см. `_reject_oversized_bootstrap_password`.
    """
    if await count_active_system_admins(session) > 0:
        return

    settings = get_settings()
    _reject_oversized_bootstrap_password(settings.bootstrap_admin_password)
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
