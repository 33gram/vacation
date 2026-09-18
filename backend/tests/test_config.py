import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.fixture(autouse=True)
def _clear_secret_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    # Не даём переменным окружения CI/локального .env подменять значения
    # по умолчанию — тесты должны видеть их независимо от того, что задано
    # снаружи (см. .github/workflows/ci.yml, шаг Run tests).
    for name in ("JWT_SECRET_KEY", "LDAP_PASSWORD_ENCRYPTION_KEY", "BOOTSTRAP_ADMIN_PASSWORD"):
        monkeypatch.delenv(name, raising=False)


def test_production_with_default_secrets_fails_to_start() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(_env_file=None, environment="production")


def test_production_with_real_secrets_starts() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        jwt_secret_key="real-secret",
        ldap_password_encryption_key="real-encryption-key-32-bytes!!!",
        bootstrap_admin_password="real-password",
    )

    assert settings.environment == "production"


def test_development_with_default_secrets_starts() -> None:
    settings = Settings(_env_file=None, environment="development")

    assert settings.jwt_secret_key == Settings.model_fields["jwt_secret_key"].default


def test_test_environment_with_default_secrets_starts() -> None:
    settings = Settings(_env_file=None, environment="test")

    default_password = Settings.model_fields["bootstrap_admin_password"].default
    assert settings.bootstrap_admin_password == default_password
