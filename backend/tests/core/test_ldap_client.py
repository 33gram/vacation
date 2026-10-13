"""`app.core.ldap_client.authenticate`/`test_connection`'s
`connect_timeout`/`receive_timeout` budget (T-M1-58, PR #171 review on
`T-M1-55`): without it, `ldap3`'s own defaults (`connect_timeout`/
`receive_timeout` both `None`) let a reachable-but-unresponsive
directory block the calling thread indefinitely - measured there at 20s
against a TCP listener that accepted a connection and never answered,
bounded only by when the OTHER side gave up, not by anything on this
side.

Two DIFFERENT failure shapes, bounded by two DIFFERENT `ldap3`
parameters, each needs its own real-socket negative control - this is
the main way this task could look closed while actually leaving one
scenario open:
  - the directory ACCEPTS the TCP connection and then never answers
    (an overloaded or stuck `slapd`) - the TCP handshake completes
    instantly, the hang is in waiting for a response. Bounded by
    `receive_timeout`.
  - the directory never even completes the TCP handshake (packets
    dropped, an unreachable route) - the hang is in `connect()` itself.
    Bounded by `connect_timeout`.
A STOPPED directory (connection refused) is neither of these - the OS
answers immediately, nothing is waiting on anything, and it is already
covered by `frontend/e2e/ldap-login.spec.ts`'s existing negative
control. Not in scope here on purpose.

Both real-socket fixtures below are self-contained (localhost only, no
firewall rules, no external network dependency, no elevated
capabilities) so they reproduce identically in the dev container and
in CI's bare `ubuntu-latest` runner (`.github/workflows/ci.yml`'s
`backend-tests` job runs pytest directly on the runner, not inside
Docker) - an approach that depended on a specific external address
being silently black-holed (a documented-reserved IP range, say) would
not have this guarantee, and was tried and dropped for exactly that
reason before landing on the techniques below:
  - `_black_hole_server` reproduces "accepts, never answers": a real
    TCP listener that `accept()`s a connection and then does nothing -
    verified by running to actually hang past receive_timeout's bound
    when receive_timeout is NOT set (not asserted here, since that
    would make every regular pytest run pay a multi-second tax; it was
    confirmed once by hand during this task, see the commit body).
  - `_hanging_connect_target` reproduces "connect itself never
    completes": a real TCP listener with `backlog=0`, pre-filled by one
    throwaway connection - any FURTHER incoming SYN is silently
    dropped by the kernel (Linux's default `tcp_abort_on_overflow=0`),
    which is indistinguishable, from the caller's side, from a dropped
    packet on an unreachable path. Verified by running directly against
    a bare socket before involving `ldap3` at all: `connect()` to it
    was still blocked past 15s with no timeout set at all.

Honest note on a premise this task's own instructions stated and this
module's development disproved: `ldap3==2.9.1`'s `receive_timeout`
(unlike `connect_timeout`) requires an `int`, not a `float` - passing a
float raises `struct.error: required argument is not an integer` from
inside `ldap3`'s own socket-handling code, reproduced by running
against the real test directory before `Settings.ldap_connect_timeout_
seconds`/`ldap_receive_timeout_seconds` were typed `int` to match (see
`app/core/config.py`). Not a premise this task's instructions stated
explicitly, but exactly the kind of thing rule 8 (CLAUDE.md) asks to
report plainly rather than route around silently.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from ldap3.core.exceptions import LDAPException

from app.core.ldap_client import LdapAuthError, LdapSettingsData, authenticate
from app.core.ldap_client import test_connection as ldap_test_connection
from tests.ldap_hang_fixtures import black_hole_server as _black_hole_server
from tests.ldap_hang_fixtures import hanging_connect_target as _hanging_connect_target

_UNREACHABLE_USER_MATCH = "mail"


def _settings_for(port: int) -> LdapSettingsData:
    return LdapSettingsData(
        server_url="127.0.0.1",
        port=port,
        base_dn="dc=company,dc=local",
        bind_dn="cn=admin,dc=company,dc=local",
        bind_password="irrelevant",
        user_match_attribute=_UNREACHABLE_USER_MATCH,
    )


# --- receive_timeout: directory accepts, never answers ----------------------


def test_authenticate_bounds_an_unresponsive_but_connected_directory() -> None:
    with _black_hole_server() as port:
        settings = _settings_for(port)
        started_at = time.monotonic()
        with pytest.raises(LdapAuthError):
            authenticate(
                settings, "ivanov@company.local", "pw", connect_timeout=2, receive_timeout=2
            )
        elapsed = time.monotonic() - started_at

    assert elapsed < 4, f"receive_timeout=2 должен был остановить bind заметно раньше ({elapsed}s)"


def test_test_connection_bounds_an_unresponsive_but_connected_directory() -> None:
    with _black_hole_server() as port:
        settings = _settings_for(port)
        started_at = time.monotonic()
        with pytest.raises(LdapAuthError):
            ldap_test_connection(settings, connect_timeout=2, receive_timeout=2)
        elapsed = time.monotonic() - started_at

    assert elapsed < 4, f"receive_timeout=2 должен был остановить bind заметно раньше ({elapsed}s)"


# --- connect_timeout: the connect() call itself never completes -------------


def test_authenticate_bounds_a_hanging_connect() -> None:
    with _hanging_connect_target() as port:
        settings = _settings_for(port)
        started_at = time.monotonic()
        with pytest.raises(LdapAuthError):
            authenticate(
                settings, "ivanov@company.local", "pw", connect_timeout=2, receive_timeout=2
            )
        elapsed = time.monotonic() - started_at

    assert elapsed < 4, f"connect_timeout=2 должен остановить connect заметно раньше ({elapsed}s)"


def test_test_connection_bounds_a_hanging_connect() -> None:
    with _hanging_connect_target() as port:
        settings = _settings_for(port)
        started_at = time.monotonic()
        with pytest.raises(LdapAuthError):
            ldap_test_connection(settings, connect_timeout=2, receive_timeout=2)
        elapsed = time.monotonic() - started_at

    assert elapsed < 4, f"connect_timeout=2 должен остановить connect заметно раньше ({elapsed}s)"


# --- translated exception and uniform message -------------------------------


def test_timeout_raises_the_same_ldap_auth_error_as_other_directory_failures() -> None:
    # FR-AUTH-001: причина наружу не детализируется - подвисший каталог
    # не должен давать иной текст/тип ошибки, чем прочие сбои каталога.
    with _black_hole_server() as port:
        settings = _settings_for(port)
        try:
            authenticate(
                settings, "ivanov@company.local", "pw", connect_timeout=1, receive_timeout=1
            )
        except LdapAuthError as exc:
            # Тот же текст, что и для прочих сбоев bind/поиска сервисной
            # записи (ldap_client.py) - не отдельная ветка форматирования
            # именно для таймаута.
            assert str(exc) == "Не удалось выполнить bind сервисной записью или поиск в каталоге"
        else:
            pytest.fail("ожидался LdapAuthError")


def test_underlying_ldap3_exceptions_are_subclasses_of_ldap_exception() -> None:
    # Честная граница механизма (см. докстринг модуля): перехват в
    # ldap_client.py - `except LDAPException`, и это работает только
    # потому, что оба исключения ldap3 на таймаут - её подклассы.
    # Проверено прогоном напрямую (не через LdapAuthError, который уже
    # перехватывает и переупаковывает) - именно это и есть то, что
    # делает перехват в ldap_client.py достаточным, без расширения.
    from ldap3.core.exceptions import LDAPSocketOpenError, LDAPSocketReceiveError

    assert issubclass(LDAPSocketOpenError, LDAPException)
    assert issubclass(LDAPSocketReceiveError, LDAPException)


# --- thread-pool behavior after repeated timeouts ----------------------------


def test_timed_out_calls_do_not_leak_threads_from_the_executor_pool() -> None:
    # T-M1-58, пункт 2(в): `asyncio.wait_for` отменяет ОЖИДАНИЕ, но не сам
    # поток - эта проверка существует для формы, где бюджет стоит на самом
    # ldap3-клиенте (эта задача), не на обёртке `wait_for` снаружи.
    # 12 последовательных зависших вызовов через `asyncio.to_thread`
    # (как и в проде - `ldap_service.py`/`ldap_settings_service.py`) не
    # должны оставить после себя 12 новых потоков - воркер пула должен
    # освобождаться и переиспользоваться каждый раз, поскольку таймаут
    # срабатывает ВНУТРИ самого синхронного вызова, а не снаружи него.
    async def _run() -> tuple[int, int]:
        with _black_hole_server() as port:
            settings = _settings_for(port)
            before = threading.active_count()
            for _ in range(12):
                with pytest.raises(LdapAuthError):
                    await asyncio.to_thread(
                        authenticate,
                        settings,
                        "ivanov@company.local",
                        "pw",
                        connect_timeout=1,
                        receive_timeout=1,
                    )
            await asyncio.sleep(0.1)
            after = threading.active_count()
            return before, after

    before, after = asyncio.run(_run())

    # Пул исполнителя может лениво создать СВОЙ единственный поток при
    # первом обращении - рост ровно на единицу от этого, не на двенадцать
    # (по одному на зависший вызов), и есть доказательство отсутствия
    # течи. Больше единицы - уже утечка, строгое равенство было бы
    # хрупким к деталям реализации пула, поэтому верхняя граница, не
    # точное число.
    assert after - before <= 1, (
        f"потоки до: {before}, после: {after} - рост больше одного "
        "говорит об утечке потоков исполнителя на зависших вызовах"
    )
