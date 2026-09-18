"""Pydantic-схемы для `/api/auth/*` (T-M1-13)."""

from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    login: str
    password: str


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
    current_password: str
    new_password: str
