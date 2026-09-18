import pytest

from app.core.crypto import DecryptionError, decrypt, encrypt


def test_encrypt_decrypt_roundtrip() -> None:
    ciphertext = encrypt("super-secret-bind-password", key="key-one")

    assert ciphertext != "super-secret-bind-password"
    assert decrypt(ciphertext, key="key-one") == "super-secret-bind-password"


def test_decrypt_with_wrong_key_fails() -> None:
    ciphertext = encrypt("super-secret-bind-password", key="key-one")

    with pytest.raises(DecryptionError):
        decrypt(ciphertext, key="key-two")
