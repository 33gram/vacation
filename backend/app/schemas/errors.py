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
