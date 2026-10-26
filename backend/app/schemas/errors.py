"""Тело ответа 400 там, где оно документируется явно (T-M1-38).

`detail` — либо одно сообщение (большинство `ValueError`-путей), либо
список нарушений парольной политики (`WeakPasswordError.violations`:
FR-AUTH-002 v1.8, «показывать все невыполненные требования сразу, а не
первое из них»; T-M1-45 — каждое нарушение теперь пара код+сообщение,
а не голая строка, см. `PasswordPolicyViolationOut`). Эндпоинты, где 400
приходит и тем, и другим путём (`POST/PUT /api/employees`,
`POST /api/auth/password/change`), документируют здесь обе формы одной
схемой — схема, называющая только списочную форму, утверждала бы о
эндпоинте больше, чем на деле гарантировано (CLAUDE.md, правило 8)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PasswordPolicyViolationOut(BaseModel):
    """Один элемент `ErrorDetail.detail`, когда причина 400 —
    `WeakPasswordError` (T-M1-45): `code` — стабильный идентификатор
    нарушенного критерия FR-AUTH-002 (см. `password_policy.
    PASSWORD_POLICY_VIOLATION_CODES`) для интерфейса, чтобы сопоставить
    с пунктом чек-листа; `message` — готовая русская формулировка
    (NFR-LOC-001), показывается как есть, не переписывается на клиенте
    (`ui.md` §7)."""

    code: str
    message: str


class ErrorDetail(BaseModel):
    detail: str | list[PasswordPolicyViolationOut]


# 401/403 приходят от общих зависимостей (`get_current_account`: нет или просрочен токен,
# гейт обязательной смены пароля; `require_roles`: роль) и нигде глобально в OpenAPI не
# описаны, поэтому роутер объявляет их у себя: `APIRouter(..., responses=AUTH_RESPONSES)`.
# Остальные коды объявляет каждый эндпоинт сам - по тому, что он действительно поднимает
# (сверка - `tests/test_documented_error_codes.py`).
AUTH_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorDetail},
    403: {"model": ErrorDetail},
}


def error_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    """`responses=` для кодов, которые эндпоинт поднимает сам: `error_responses(400, 404)`."""
    return {code: {"model": ErrorDetail} for code in codes}
