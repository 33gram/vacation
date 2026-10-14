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

    PR #172 review (предыдущая редакция этого абзаца, «сумма двух
    соединений, около 10 с, не 5» — ОШИБОЧНА, не соответствует ни одному
    из двух реальных режимов отказа; ниже — измеренное, не выведенное
    арифметически):

    РЕЖИМ ЗАВИСАНИЯ (каталог принял соединение, но не отвечает НИ НА ЧТО):
    первая истёкшая фаза поднимает `LDAPException` внутри `try` первого
    соединения, который перехватывается и сразу уходит `LdapAuthError`
    наружу — ВТОРОЕ соединение не открывается, суммы двух бюджетов не
    возникает. Измерено (боевые значения `connect_timeout=5`,
    `receive_timeout=5`, реальный тестовый `openldap`, см. `infra/
    docker-compose.yml`; `tests/core/test_ldap_client.py`): зависание на
    приёме ответа (TCP-слушатель, принявший соединение и не ответивший
    ничего) — 5.011 с; зависание на самом `connect()` (SYN молча
    отбрасывается ядром) — тоже 5.011 с. Один вход при зависании ограничен
    ОДНОЙ фазой, не суммой.

    РЕЖИМ МЕДЛЕННОГО, НО ОТВЕЧАЮЩЕГО КАТАЛОГА: бюджет применяется к
    КАЖДОМУ обмену отдельно, а обменов не два — на один вызов этой
    функции (посчитано `ldap3`'s `collect_usage=True` против реального
    тестового `openldap`) приходится 3 реальные LDAP-операции (bind
    сервисной записью + наш `search` в первом соединении, bind найденным
    DN во втором) при `get_info="NONE"` (см. ниже, почему именно этот
    режим выбран) — 2 bind + 1 search, ~28 байт входящего трафика.
    Измерено прокси, задерживающим каждый TCP-чанк ответа на 1.5 с (под
    бюджетом, чтобы ни одна фаза не истекла сама по себе), 5 прогонов:
    среднее 4.55 с — совпадает с 3 обменами × 1.5 с с точностью до
    десятых. При старом `get_info="SCHEMA"` (дефолт самой `ldap3`,
    `inspect.signature(Server.__init__)`) те же 5 прогонов дали
    13.6–16.7 с (среднее 15.17 с): `ldap3` при каждом `auto_bind`
    САМОСТОЙНО читает DSE и схему — это ещё 2 `search`-операции НА
    КАЖДОЕ из двух соединений (итого 7 реальных операций, не 3, и
    схема НЕ кэшируется между двумя `Connection` одного `Server`,
    перепрочитывается оба раза), плюс ~83 КБ схемы, которые на
    медленном канале сами по себе требуют нескольких TCP-чанков.
    Множитель оказался большим — это и привело к решению ниже.

    ПОЧЕМУ `get_info="NONE"` (п. 2 того же круга ревью): приложению
    схема каталога не нужна ни для чего — поиском по `app/` подтверждено
    отсутствие обращений к `server.schema`/`server.info`/`subschema`.
    Разница измерена выше (3 реальных обмена и ~28 байт против 7 и
    ~83 КБ, ~3х по времени при одинаковой задержке на обмен) — значима,
    и схема действительно не используется, поэтому оба построения
    `Server(...)` в этом модуле явно передают `get_info="NONE"`, а не
    молчат на дефолте самой `ldap3`.

    С учётом этого: предельное время ОДНОГО входа у здорового, но
    МЕДЛЕННОГО каталога — сумма трёх обменов, каждый не дольше своего
    бюджета (не 10 с при 5/5 и не бесконечность — но и не «один бюджет на
    весь вызов»); у ЗАВИСШЕГО каталога — ровно одна фаза (5 с при 5/5),
    потому что до второго соединения дело не доходит. Для
    `test_connection`, которая открывает только ОДНО соединение (bind +
    один `search`, 2 реальных обмена при `get_info="NONE"`), и то, и то
    верно в облегчённом варианте: зависание — одна фаза, медленный ответ —
    сумма из двух, не трёх, обменов.

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
        # PR #172 review, п. 2: приложение не читает схему каталога (поиском
        # по app/ подтверждено) - `ldap3`'s дефолт `get_info=SCHEMA` на
        # каждый bind добавлял 2 лишних `search`-обмена на DSE/схему
        # (не кэшируются между двумя Connection этого Server - перепрочитаны
        # оба раза) и ~83 КБ бесполезного трафика, измерено против
        # реального тестового openldap (см. докстринг выше). Явный выбор,
        # не дефолт библиотеки.
        get_info="NONE",
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
    тот же бюджет, та же причина, намеренно общий для обоих путей.
    `get_info="NONE"` — та же находка и то же измерение, см. там же:
    одно соединение здесь даёт 2 реальных обмена (bind + search), не 4
    (bind + 2 обмена DSE/схемы + search), которые добавлял дефолт
    `ldap3`."""
    server = Server(
        settings.server_url,
        port=settings.port,
        use_ssl=settings.port == 636,
        connect_timeout=connect_timeout,
        get_info="NONE",
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
