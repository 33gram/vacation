import datetime

import jwt
import pytest

from app.core.config import get_settings
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_hash_password_roundtrip() -> None:
    password_hash = hash_password("CorrectHorseBatteryStaple")

    assert password_hash != "CorrectHorseBatteryStaple"
    assert verify_password("CorrectHorseBatteryStaple", password_hash)


def test_verify_password_rejects_wrong_password() -> None:
    password_hash = hash_password("CorrectHorseBatteryStaple")

    assert not verify_password("wrong-password", password_hash)


def test_access_token_roundtrip() -> None:
    token = create_access_token(account_id=42, role="hr_admin")

    payload = decode_token(token)

    assert payload["sub"] == "42"
    assert payload["role"] == "hr_admin"
    assert payload["type"] == "access"


def test_refresh_token_roundtrip() -> None:
    token = create_refresh_token(account_id=42)

    payload = decode_token(token)

    assert payload["sub"] == "42"
    assert payload["type"] == "refresh"


def test_decode_token_rejects_tampered_signature() -> None:
    token = create_access_token(account_id=1, role="employee")
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

    with pytest.raises(TokenError):
        decode_token(tampered)


def test_decode_token_rejects_expired_token() -> None:
    settings = get_settings()
    now = datetime.datetime.now(datetime.UTC)
    expired_payload = {
        "sub": "1",
        "role": "employee",
        "type": "access",
        "iat": now - datetime.timedelta(hours=2),
        "exp": now - datetime.timedelta(hours=1),
    }
    expired_token = jwt.encode(
        expired_payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm
    )

    with pytest.raises(TokenError):
        decode_token(expired_token)
