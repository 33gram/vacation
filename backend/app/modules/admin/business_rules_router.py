"""`/api/admin/business-rules` (T-M2-12, FR-ADMIN-004).

Доступ - `hr_admin` и `system_admin`: строка матрицы раздела 7 «Настройка параметров
бизнес-правил» (требования v1.27, раздел 13 п. 33). Остальные `/api/admin/*` заперты на
`system_admin` по своим строкам матрицы; здесь это не копируется.

`GET` отдаёт ВЕСЬ реестр (`RULES`), `PUT` меняет значения СУЩЕСТВУЮЩИХ ключей набором.
Создать или удалить ключ эндпоинтом нельзя: реестр живёт в коде, строку заводит миграция
(неизвестный ключ - 404).

Коды (правило проекта, `CLAUDE.md`): негодное значение - 400 (запрос недопустим сам по себе,
в ответе названы ВСЕ негодные ключи), неизвестный ключ - 404. Конфликта с уже записанными
данными здесь нет, поэтому 409 эндпоинт не отдаёт.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.account import Account
from app.models.business_rule_config import BusinessRuleConfig
from app.modules.auth.rbac import require_roles
from app.modules.auth.repository import get_author_display_names
from app.modules.business_rules import service
from app.schemas.business_rule import BusinessRuleOut, BusinessRulesUpdate
from app.schemas.errors import AUTH_RESPONSES, error_responses

router = APIRouter(
    prefix="/api/admin/business-rules", tags=["business-rules"], responses=AUTH_RESPONSES
)

_require_rules_admin = require_roles("hr_admin", "system_admin")


async def _to_out(
    session: AsyncSession, rows: list[BusinessRuleConfig]
) -> list[BusinessRuleOut]:
    names = await get_author_display_names(
        session, {r.updated_by_account_id for r in rows if r.updated_by_account_id is not None}
    )
    return [
        BusinessRuleOut(
            key=row.key,
            value=row.value,
            value_type=row.value_type,
            description=row.description,
            minimum=service.RULES[row.key].minimum,
            maximum=service.RULES[row.key].maximum,
            updated_at=row.updated_at,
            updated_by_name=(
                names.get(row.updated_by_account_id) if row.updated_by_account_id else None
            ),
        )
        for row in rows
    ]


@router.get("", response_model=list[BusinessRuleOut])
async def get_business_rules(
    session: AsyncSession = Depends(get_db_session),
    _: Account = Depends(_require_rules_admin),
) -> list[BusinessRuleOut]:
    return await _to_out(session, await service.list_rule_rows(session))


@router.put(
    "", response_model=list[BusinessRuleOut], responses=error_responses(400, 404)
)
async def put_business_rules(
    payload: BusinessRulesUpdate,
    session: AsyncSession = Depends(get_db_session),
    account: Account = Depends(_require_rules_admin),
) -> list[BusinessRuleOut]:
    try:
        rows = await service.update_rules(
            session, payload.values, actor_account_id=account.id
        )
    except service.RuleKeysNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except service.RuleValuesInvalidError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return await _to_out(session, rows)
