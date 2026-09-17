import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_production_with_default_secrets_fails_to_start() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(environment="production")


def test_production_with_real_secrets_starts() -> None:
    settings = Settings(
        environment="production",
        jwt_secret_key="real-secret",
        field_encryption_key="real-encryption-key-32-bytes!!!",
        bootstrap_admin_password="real-password",
    )

    assert settings.environment == "production"


def test_development_with_default_secrets_starts() -> None:
    settings = Settings(environment="development")

    assert settings.jwt_secret_key == Settings.model_fields["jwt_secret_key"].default


def test_test_environment_with_default_secrets_starts() -> None:
    settings = Settings(environment="test")

    default_password = Settings.model_fields["bootstrap_admin_password"].default
    assert settings.bootstrap_admin_password == default_password
