"""LDAP-клиент для bind-аутентификации (T-M1-06, FR-AUTH-001).

Синхронный (ldap3 не имеет asyncio API) — вызывающий сервисный код должен
выполнять `authenticate` через `asyncio.to_thread`, чтобы не блокировать
event loop.
"""

from __future__ import annotations

from dataclasses import dataclass

from ldap3 import Connection, Server
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars


class LdapAuthError(Exception):
    """Bind не удался: неверные учётные данные, сбой соединения или
    некорректные настройки. Причина намеренно не детализируется наружу —
    единообразный ответ вызывающей стороне (FR-AUTH-001)."""


@dataclass(frozen=True)
class LdapSettingsData:
    server_url: str
    port: int
    base_dn: str
    bind_dn: str
    bind_password: str
    user_match_attribute: str = "mail"


def authenticate(settings: LdapSettingsData, login: str, password: str) -> str:
    """Ищет пользователя по `user_match_attribute` (bind сервисной записью),
    затем проверяет пароль bind'ом от имени найденного DN.

    Возвращает значение `user_match_attribute` из каталога — источник
    истины для сопоставления с `employees.email` (FR-AUTH-001), а не то,
    что ввёл пользователь.

    Поднимает `LdapAuthError` при любой неудаче: пользователь не найден,
    неоднозначное совпадение, неверный пароль, сбой сервисного bind'а.
    """
    server = Server(settings.server_url, port=settings.port, use_ssl=settings.port == 636)

    try:
        with Connection(
            server,
            user=settings.bind_dn,
            password=settings.bind_password,
            auto_bind=True,
        ) as service_conn:
            search_filter = (
                f"({settings.user_match_attribute}="
                f"{escape_filter_chars(login)})"
            )
            service_conn.search(
                search_base=settings.base_dn,
                search_filter=search_filter,
                attributes=[settings.user_match_attribute],
            )
            entries = service_conn.entries
            if len(entries) != 1:
                raise LdapAuthError("User not found or match is ambiguous")

            user_dn = entries[0].entry_dn
            matched_value = str(entries[0][settings.user_match_attribute].value)
    except LDAPException as exc:
        raise LdapAuthError("Service bind or directory search failed") from exc

    try:
        with Connection(server, user=user_dn, password=password, auto_bind=True):
            pass
    except LDAPException as exc:
        raise LdapAuthError("Invalid credentials") from exc

    return matched_value
