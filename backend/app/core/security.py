"""Хэширование локальных паролей (bcrypt, NFR-SEC-004) и выпуск/проверка
JWT-сессий (PyJWT, FR-AUTH-001/002)."""

from __future__ import annotations

import datetime
from typing import Any

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.time_utils import utcnow


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # некорректный формат хэша — не должно случаться, но не роняем логин
        return False


class TokenError(Exception):
    """JWT не прошёл декодирование/проверку подписи или срока действия."""


def create_access_token(*, account_id: int, role: str) -> str:
    settings = get_settings()
    now = utcnow()
    payload = {
        "sub": str(account_id),
        "role": role,
        "type": "access",
        "iat": now,
        "exp": now + datetime.timedelta(minutes=settings.jwt_access_token_ttl_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(*, account_id: int) -> str:
    settings = get_settings()
    now = utcnow()
    payload = {
        "sub": str(account_id),
        "type": "refresh",
        "iat": now,
        "exp": now + datetime.timedelta(days=settings.jwt_refresh_token_ttl_days),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
