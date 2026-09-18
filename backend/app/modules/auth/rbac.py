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
    if account is None or not account.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not found or inactive")

    return account


def require_roles(*roles: str) -> Callable[[Account], Awaitable[Account]]:
    async def _dependency(account: Account = Depends(get_current_account)) -> Account:
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
