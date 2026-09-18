"""Обратимое шифрование на уровне приложения (T-M1-04, T-000).

Применяется только к `ldap_settings.bind_password_encrypted` — паролю
служебной записи LDAP. Ключ — `LDAP_PASSWORD_ENCRYPTION_KEY`.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


class DecryptionError(Exception):
    """Значение не удалось расшифровать текущим ключом."""


def _fernet(key: str) -> Fernet:
    # Fernet требует 32-байтный url-safe base64 ключ; произвольная строка из
    # переменной окружения приводится к этому формату через SHA-256.
    derived = base64.urlsafe_b64encode(hashlib.sha256(key.encode("utf-8")).digest())
    return Fernet(derived)


def encrypt(plaintext: str, key: str) -> str:
    return _fernet(key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str, key: str) -> str:
    try:
        return _fernet(key).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise DecryptionError("Unable to decrypt value with the configured key") from exc
