"""Парольная политика для локальных учётных записей (T-M1-25, FR-AUTH-002,
NFR-SEC-004).

Значения по умолчанию из требований; настраиваемость через
`business_rules_config` появится в M2 (T-M2-09) — здесь захардкожены
значения по умолчанию, указанные в требованиях.
"""

from __future__ import annotations

import datetime

DEFAULT_MIN_PASSWORD_LENGTH = 12
DEFAULT_MAX_FAILED_ATTEMPTS = 5
DEFAULT_LOCKOUT_DURATION = datetime.timedelta(minutes=15)


class WeakPasswordError(Exception):
    """Пароль не соответствует минимальной политике."""


def validate_password_strength(
    password: str, *, min_length: int = DEFAULT_MIN_PASSWORD_LENGTH
) -> None:
    if len(password) < min_length:
        raise WeakPasswordError(f"Password must be at least {min_length} characters long")
