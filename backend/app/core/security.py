"""Хэширование локальных паролей (bcrypt, NFR-SEC-004) и выпуск/проверка
JWT-сессий (PyJWT, FR-AUTH-001/002)."""

from __future__ import annotations

import datetime
from typing import Any

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.time_utils import utcnow

# PR #140 review (четвёртый круг): 72 байта — свойство bcrypt (он молча
# хэширует только первые 72 байта входа, не отклоняя более длинный),
# а не парольной политики приложения, поэтому граница живёт здесь, а не
# в `modules/auth/password_policy.py`. Три места приложения, которым
# нужно было проверить это ДО вызова bcrypt (парольная политика при
# задании/смене пароля, вход, bootstrap-администратор), трижды
# передержали проверку заново вместо общего предиката — граница стоила
# трёх кругов ревью именно поэтому, а не из-за расхождения копий числа
# 72 между ними.
BCRYPT_MAX_INPUT_BYTES = 72


def exceeds_bcrypt_input_limit(password: str) -> bool:
    return len(password.encode("utf-8")) > BCRYPT_MAX_INPUT_BYTES


def hash_password(password: str) -> str:
    if exceeds_bcrypt_input_limit(password):
        # Сетка НЕ вместо дружелюбных проверок выше по стеку (парольная
        # политика, вход, bootstrap) — они остаются и дают понятное
        # сообщение раньше. Это срабатывает только тогда, когда новый
        # путь к `hash_password` их не вызвал, то есть на баге, а не на
        # штатном отказе пользователю.
        raise ValueError(
            f"Password is {len(password.encode('utf-8'))} bytes when UTF-8 encoded, "
            f"over bcrypt's {BCRYPT_MAX_INPUT_BYTES}-byte input limit — reject it "
            "before hashing instead of letting bcrypt silently truncate it"
        )
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
