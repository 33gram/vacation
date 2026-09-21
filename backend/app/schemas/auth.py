"""Pydantic-схемы для `/api/auth/*` (T-M1-13)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.modules.auth.password_policy import MAX_PASSWORD_LENGTH_CHARS


class LoginRequest(BaseModel):
    login: str
    # PR #140 review: без границы запрос с паролем размером в мегабайты
    # доходил бы до bcrypt-хэширования нетронутым. Значение — грубый
    # запас поверх точной границы в 72 БАЙТА (`password_policy.
    # MAX_PASSWORD_BYTES`), а не сама эта граница: здесь только символы
    # отсекаются до бизнес-логики, точная проверка в байтах — в ней.
    password: str = Field(max_length=MAX_PASSWORD_LENGTH_CHARS)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    role: str
    must_change_password: bool


class MeResponse(BaseModel):
    id: int
    role: str
    is_active: bool
    must_change_password: bool
    employee_id: int | None
    login: str | None
    embedded_mode: bool


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(max_length=MAX_PASSWORD_LENGTH_CHARS)
    new_password: str = Field(max_length=MAX_PASSWORD_LENGTH_CHARS)
