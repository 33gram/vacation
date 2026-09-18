"""RBAC-зависимость FastAPI (T-M1-09, NFR-SEC-003, раздел 7 требований).

Переиспользуется всеми последующими модулями с эндпоинтами (ветки после
этой). Роль «руководитель» не хранится в `accounts.role` — проверяется
через `employees.manager_id` (`is_manager_of`), см. `plan.md` §5.1.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenError, decode_token
from app.db.session import get_db_session
from app.models.account import Account
from app.models.employee import Employee
from app.modules.auth.access_control import enforce_access

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_account(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> Account:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    try:
        payload = decode_token(credentials.credentials)
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from exc

    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token type")

    account = await session.get(Account, int(payload["sub"]))
    if account is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not found or inactive")

    # FR-AUTH-004: та же проверка, что и на входе (`local_service`/
    # `ldap_service` вызывают `enforce_access` напрямую) —
    # деактивированная карточка сотрудника или отключённая учётная запись
    # перестают авторизовывать запросы немедленно, а не только после
    # истечения TTL access-токена. `AccessDeniedError` доходит до
    # обработчика в `main.py` и превращается в 403.
    await enforce_access(session, account)

    return account


def require_roles(*roles: str) -> Callable[[Account], Awaitable[Account]]:
    """T-M1-13: пока `account.must_change_password` истинно, доступны
    только `GET /api/auth/me` и `POST /api/auth/password/change` (они
    используют `get_current_account` напрямую, без `require_roles`) —
    любой эндпоинт за этой зависимостью требует сначала сменить пароль."""

    async def _dependency(account: Account = Depends(get_current_account)) -> Account:
        if account.must_change_password:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Password change required before continuing",
            )
        if account.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
        return account

    return _dependency


async def is_manager_of(
    session: AsyncSession, manager_employee_id: int, target_employee_id: int
) -> bool:
    """Прямой руководитель целевого сотрудника (`employees.manager_id`)."""
    target = await session.get(Employee, target_employee_id)
    return target is not None and target.manager_id == manager_employee_id
