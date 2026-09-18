"""RBAC-зависимость FastAPI (T-M1-09, NFR-SEC-003, раздел 7 требований).

Переиспользуется всеми последующими модулями с эндпоинтами (ветки после
этой). Роль «руководитель» не хранится в `accounts.role` — проверяется
через `employees.manager_id`, см. `plan.md` §5.1.

Два разных примитива подчинения, не путать (`plan.md` §5.1):
`is_direct_manager_of` — только прямое подчинение, для МАРШРУТА
согласования (`FR-APPR-001`); `is_in_management_chain` и
`list_subordinate_employee_ids` — вся цепочка/поддерево, для ПРАВА
просматривать карточки («своя команда») и, начиная с M3, закрывать шаг
согласования «руководитель». Имена намеренно не похожи друг на друга.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.security import TokenError, decode_token
from app.db.session import get_db_session
from app.models.account import Account
from app.models.employee import Employee
from app.modules.auth.access_control import enforce_access

# Ограничение глубины обхода цепочки/поддерева подчинения — вторая линия
# защиты от зацикливания вдобавок к запрету циклов при записи
# (`T-M1-10`/`T-M1-15`): без неё цикл в данных, попавший в обход API
# (например, будущий импорт, `T-M1-11`), увёл бы рекурсивный запрос в
# бесконечность. Штат до 750 человек (`NFR-SCALE-001`) не создаёт
# организационных цепочек такой глубины ни при каком реалистичном
# сценарии — лимит только на случай испорченных данных.
MAX_MANAGEMENT_CHAIN_DEPTH = 1000

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


async def is_direct_manager_of(
    session: AsyncSession, manager_employee_id: int, target_employee_id: int
) -> bool:
    """Прямой руководитель целевого сотрудника (`employees.manager_id`) —
    определяет МАРШРУТ согласования (`FR-APPR-001`), а не право
    просмотра/согласования (см. `is_in_management_chain`)."""
    target = await session.get(Employee, target_employee_id)
    return target is not None and target.manager_id == manager_employee_id


async def is_in_management_chain(
    session: AsyncSession, manager_employee_id: int, target_employee_id: int
) -> bool:
    """Стоит ли `manager_employee_id` где-либо выше `target_employee_id`
    по цепочке `manager_id` — прямо или через любое число промежуточных
    руководителей. Определяет ПРАВО просматривать карточку («своя
    команда», `plan.md` §5.1), а не маршрут согласования — для этого
    есть отдельная, узкая `is_direct_manager_of`.

    Сотрудник сам себе не «вышестоящий»: гарантируется явной проверкой
    ниже, а не тем, что обход цепочки вверх не может вернуться к
    исходной точке — при цикле в данных (`A.manager_id = B`,
    `B.manager_id = A`, запрет циклов при записи — первая, но не
    единственная линия защиты) он ровно это и делает уже на втором шаге,
    и без явной проверки `is_in_management_chain(A, A)` дал бы `True`.

    Один рекурсивный SQL-запрос (`WITH RECURSIVE` через `.cte(recursive=
    True)`), а не цикл с N обращениями к БД."""
    if manager_employee_id == target_employee_id:
        return False

    chain = (
        select(Employee.manager_id.label("current_manager_id"), literal(1).label("depth"))
        .where(Employee.id == target_employee_id)
        .cte("management_chain", recursive=True)
    )
    parent = aliased(Employee)
    chain = chain.union_all(
        select(parent.manager_id, chain.c.depth + 1)
        .select_from(chain)
        .join(parent, parent.id == chain.c.current_manager_id)
        .where(chain.c.depth < MAX_MANAGEMENT_CHAIN_DEPTH)
    )
    result = await session.execute(
        select(literal(1))
        .select_from(chain)
        .where(chain.c.current_manager_id == manager_employee_id)
        .limit(1)
    )
    return result.first() is not None


async def list_subordinate_employee_ids(
    session: AsyncSession, manager_employee_id: int
) -> list[int]:
    """Все прямые и косвенные подчинённые `manager_employee_id` (всё
    поддерево, без самого `manager_employee_id`) одним запросом —
    списку (`GET /api/employees`) для руководителя нужна ровно эта
    операция, а не проверка `is_in_management_chain` по каждой строке
    (иначе N+1).

    Исключение `manager_employee_id` из результата гарантируется явным
    фильтром в финальном `SELECT`, а не тем, что обход вниз по дереву не
    может вернуться к исходной точке — при цикле в данных (запрет циклов
    при записи — первая, но не единственная линия защиты) обход именно
    это и делает, и без явного фильтра `manager_employee_id` попал бы в
    результат."""
    subtree = (
        select(Employee.id.label("id"), literal(0).label("depth"))
        .where(Employee.manager_id == manager_employee_id)
        .cte("management_subtree", recursive=True)
    )
    child = aliased(Employee)
    subtree = subtree.union_all(
        select(child.id, subtree.c.depth + 1)
        .select_from(subtree)
        .join(child, child.manager_id == subtree.c.id)
        .where(subtree.c.depth < MAX_MANAGEMENT_CHAIN_DEPTH)
    )
    result = await session.execute(
        select(subtree.c.id).where(subtree.c.id != manager_employee_id)
    )
    return [row[0] for row in result.all()]
