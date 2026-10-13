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

# Единственное место для дефолтов подключения к LDAP: значения также
# используются в `schemas/ldap_settings.py` и в фолбэке
# `admin/ldap_settings_service.py`, чтобы не дублировать их там. DB-дефолт
# в `models/ldap_settings.py` (и `server_default` в миграции 0004) сюда не
# входит — это отдельный, неизменяемый слой (защита для вставок мимо
# ORM/приложения), а не то же самое бизнес-правило.
DEFAULT_LDAP_PORT = 389
DEFAULT_USER_MATCH_ATTRIBUTE = "mail"


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
    user_match_attribute: str = DEFAULT_USER_MATCH_ATTRIBUTE


def authenticate(
    settings: LdapSettingsData,
    login: str,
    password: str,
    *,
    connect_timeout: int,
    receive_timeout: int,
) -> str:
    """Ищет пользователя по `user_match_attribute` (bind сервисной записью),
    затем проверяет пароль bind'ом от имени найденного DN.

    Возвращает значение `user_match_attribute` из каталога — источник
    истины для сопоставления с `employees.email` (FR-AUTH-001), а не то,
    что ввёл пользователь.

    `connect_timeout`/`receive_timeout` — бюджет ожидания одного обращения
    (T-M1-58, `Settings.ldap_connect_timeout_seconds`/`ldap_receive_
    timeout_seconds`): без них `ldap3` ждёт ответа каталога неограниченно
    долго (проверено прогоном — TCP-сервер, принимающий соединение и
    никогда не отвечающий, держал вызов ровно столько, сколько сам не
    отвечал). Обязательные параметры, не со значением по умолчанию здесь —
    единственный источник дефолта это `Settings`, второго не завести.

    Поднимает `LdapAuthError` при любой неудаче: пользователь не найден,
    неоднозначное совпадение, неверный пароль, сбой сервисного bind'а,
    истечение бюджета ожидания (`LDAPSocketOpenError`/`LDAPSocketReceiveError`
    — оба подклассы `LDAPException`, проверено прогоном).
    """
    server = Server(
        settings.server_url,
        port=settings.port,
        use_ssl=settings.port == 636,
        connect_timeout=connect_timeout,
    )

    try:
        with Connection(
            server,
            user=settings.bind_dn,
            password=settings.bind_password,
            auto_bind=True,
            receive_timeout=receive_timeout,
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
                raise LdapAuthError("Пользователь не найден или совпадение неоднозначно")

            user_dn = entries[0].entry_dn
            matched_value = str(entries[0][settings.user_match_attribute].value)
    except LDAPException as exc:
        raise LdapAuthError(
            "Не удалось выполнить bind сервисной записью или поиск в каталоге"
        ) from exc

    try:
        with Connection(
            server,
            user=user_dn,
            password=password,
            auto_bind=True,
            receive_timeout=receive_timeout,
        ):
            pass
    except LDAPException as exc:
        raise LdapAuthError("Неверные учётные данные") from exc

    return matched_value


def test_connection(
    settings: LdapSettingsData,
    *,
    connect_timeout: int,
    receive_timeout: int,
) -> None:
    """Проверяет bind сервисной записью и доступность `base_dn` — без
    аутентификации конкретного пользователя (T-M1-16,
    `POST /api/admin/ldap-settings/test`). Поднимает `LdapAuthError` при
    неудаче; ничего не возвращает и не сохраняет.

    `connect_timeout`/`receive_timeout` — см. докстринг `authenticate`:
    тот же бюджет, та же причина, намеренно общий для обоих путей."""
    server = Server(
        settings.server_url,
        port=settings.port,
        use_ssl=settings.port == 636,
        connect_timeout=connect_timeout,
    )
    try:
        with Connection(
            server,
            user=settings.bind_dn,
            password=settings.bind_password,
            auto_bind=True,
            receive_timeout=receive_timeout,
        ) as conn:
            conn.search(search_base=settings.base_dn, search_filter="(objectClass=*)", size_limit=1)
    except LDAPException as exc:
        raise LdapAuthError(
            "Не удалось выполнить bind сервисной записью или поиск по base DN"
        ) from exc
