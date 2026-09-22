"""Тело ответа 400 там, где оно документируется явно (T-M1-38).

`detail` — либо одно сообщение (большинство `ValueError`-путей), либо
список (`WeakPasswordError.violations`: FR-AUTH-002 v1.8, «показывать
все невыполненные требования сразу, а не первое из них»). Эндпоинты,
где 400 приходит и тем, и другим путём (`POST/PUT /api/employees`,
`POST /api/auth/password/change`), документируют здесь обе формы одной
схемой — схема, называющая только списочную форму, утверждала бы о
эндпоинте больше, чем на деле гарантировано (CLAUDE.md, правило 8)."""

from __future__ import annotations

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    detail: str | list[str]
