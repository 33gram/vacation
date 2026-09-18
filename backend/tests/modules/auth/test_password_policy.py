import pytest

from app.modules.auth.password_policy import WeakPasswordError, validate_password_strength


def test_password_meeting_minimum_length_is_accepted() -> None:
    validate_password_strength("TwelveCharsOk")


def test_password_below_minimum_length_is_rejected() -> None:
    with pytest.raises(WeakPasswordError):
        validate_password_strength("short1")
