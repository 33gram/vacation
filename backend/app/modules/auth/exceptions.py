class AuthError(Exception):
    """Базовый класс ошибок аутентификации/авторизации модуля auth."""


class AuthenticationError(AuthError):
    """Неверные учётные данные (пароль/bind) — 401."""


class AccessDeniedError(AuthError):
    """Аутентификация прошла, но общие проверки доступа не пройдены
    (FR-AUTH-004: нет карточки сотрудника/деактивирована/учётная запись
    отключена) — 403."""


class LdapNotConfiguredError(AuthError):
    """Нет активной записи `ldap_settings` — Система работает в режиме
    внедрения (FR-AUTH-003)."""
