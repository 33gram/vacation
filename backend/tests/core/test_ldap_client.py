"""`app.core.ldap_client.authenticate`/`test_connection`'s
`connect_timeout`/`receive_timeout` budget (T-M1-58, PR #171 review on
`T-M1-55`): without either, `ldap3`'s own defaults (both `None`) let a
reachable-but-unresponsive directory block the calling thread
indefinitely - measured there at 20s against a TCP listener that
accepted a connection and never answered, bounded only by when the
OTHER side gave up, not by anything on this side.

PR #172 review (blocking finding): the ACTUAL mechanics are not "two
independently-bounded failure modes", and the first edition of this
module claimed exactly that - wrong, verified by running against
`ldap3==2.9.1`. `connect_timeout` bounds the `connect()` phase. When
`receive_timeout` is ALSO given, it independently governs the
subsequent receive-wait - not a narrowing of `connect_timeout`'s value,
a full replacement that can be smaller OR LARGER than `connect_timeout`
(`connect_timeout=5, receive_timeout=10` failed at 10.02s, not 5s;
`connect_timeout=1, receive_timeout=10` failed at 10.02s too - entirely
unbounded by the smaller `connect_timeout`). When `receive_timeout` is
NOT given at all, the receive phase falls back to whatever
`connect_timeout` set on the underlying socket, because nothing resets
it for the switch into the read - this is WHY a socket that accepts the
connection and stays silent, with no `receive_timeout` at all, still
failed bounded by `connect_timeout` alone (2.01s at `connect_timeout=2`,
8.01s at `connect_timeout=8`). Read casually, that looks like "connect_
timeout is a ceiling receive_timeout narrows within" - the first
edition of this module said exactly that, and it is wrong: there is no
ceiling relationship, only a fallback when `receive_timeout` is absent.

Two scenarios are still tested separately below, but for a narrower,
correct reason than "two parameters, two modes": one scenario proves
`connect_timeout` bounds a hang that happens INSIDE `connect()` itself
(nothing for `receive_timeout` to do there - it is kept generous in
that test on purpose, so the test's own pass/fail turns on `connect_
timeout` alone). The other proves `receive_timeout`, when given,
actually GOVERNS the receive-phase wait rather than doing nothing -
which requires giving `connect_timeout` a GENEROUS value and `receive_
timeout` a TIGHT one, and asserting the observed time tracks the tight
bound, not the generous one. A test that gives both parameters the
same value (the first edition's mistake) cannot tell "receive_timeout
worked" apart from "connect_timeout was doing all the work via the
fallback and receive_timeout did nothing" - removing `receive_timeout=`
from the real call left both of that edition's receive-wait tests just
as green as before (bounded by the fallback to `connect_timeout`
instead), which is exactly how this was caught (see the commit body for
the measurement). A STOPPED directory (connection refused) is neither
scenario - the OS answers immediately, nothing is waiting on anything,
and it is already covered by `frontend/e2e/ldap-login.spec.ts`'s
existing negative control. Not in scope here on purpose.

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
    verified by running to actually hang past `connect_timeout`'s own
    bound when NEITHER timeout is set (not asserted here, since that
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
`app/core/config.py`). `connect_timeout` itself accepts a float value
silently (no error) - the two parameters are not symmetric in this
respect either, confirmed by running; both fields are `PositiveInt` in
`Settings` regardless, so this difference is unreachable through the
environment-variable path (a string that parses as a float fails int
validation for both fields before either ever reaches `ldap3`).
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from ldap3 import Connection
from ldap3.core.exceptions import LDAPException

import app.core.ldap_client as _ldap_client_module
from app.core.ldap_client import LdapAuthError, LdapSettingsData, authenticate
from app.core.ldap_client import test_connection as ldap_test_connection
from tests.ldap_guard import LDAP_TEST_HOST, LDAP_TEST_PORT, requires_ldap
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


# --- receive_timeout actually GOVERNS the receive wait, not just "has a
# --- value that happens not to matter" ---------------------------------
#
# connect_timeout generous (8s), receive_timeout tight (1s): the observed
# time must track the TIGHT bound, not the generous one. Giving both
# parameters the same value (the bug this round fixed) cannot tell
# "receive_timeout worked" apart from "connect_timeout's fallback was
# doing all the bounding, because nothing overrode it" - removing
# receive_timeout= from the real call left the old version of these two
# tests just as green, since connect_timeout=2's fallback bounded the
# hang on its own either way (see the module docstring for why this is a
# fallback, not a ceiling receive_timeout narrows within).


def test_authenticate_bounds_the_receive_wait_tighter_than_connect() -> None:
    with _black_hole_server() as port:
        settings = _settings_for(port)
        started_at = time.monotonic()
        with pytest.raises(LdapAuthError):
            authenticate(
                settings, "ivanov@company.local", "pw", connect_timeout=8, receive_timeout=1
            )
        elapsed = time.monotonic() - started_at

    assert elapsed < 4, (
        f"receive_timeout=1 должен был остановить bind заметно раньше, чем "
        f"connect_timeout=8 ({elapsed}s)"
    )


def test_test_connection_bounds_the_receive_wait_tighter_than_connect() -> None:
    with _black_hole_server() as port:
        settings = _settings_for(port)
        started_at = time.monotonic()
        with pytest.raises(LdapAuthError):
            ldap_test_connection(settings, connect_timeout=8, receive_timeout=1)
        elapsed = time.monotonic() - started_at

    assert elapsed < 4, (
        f"receive_timeout=1 должен был остановить bind заметно раньше, чем "
        f"connect_timeout=8 ({elapsed}s)"
    )


# --- connect_timeout: the connect() call itself never completes -------------
#
# receive_timeout generous (8s) here on purpose - the hang is INSIDE
# connect(), before receive is ever reached, so receive_timeout cannot be
# what bounds it; keeping it generous makes each test's pass/fail turn on
# connect_timeout alone, not on an ambiguous combination of both.


def test_authenticate_bounds_a_hanging_connect() -> None:
    with _hanging_connect_target() as port:
        settings = _settings_for(port)
        started_at = time.monotonic()
        with pytest.raises(LdapAuthError):
            authenticate(
                settings, "ivanov@company.local", "pw", connect_timeout=2, receive_timeout=8
            )
        elapsed = time.monotonic() - started_at

    assert elapsed < 4, f"connect_timeout=2 должен остановить connect заметно раньше ({elapsed}s)"


def test_test_connection_bounds_a_hanging_connect() -> None:
    with _hanging_connect_target() as port:
        settings = _settings_for(port)
        started_at = time.monotonic()
        with pytest.raises(LdapAuthError):
            ldap_test_connection(settings, connect_timeout=2, receive_timeout=8)
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


# --- get_info=LDAP_GET_INFO_NONE закреплено поведением, не комментарием -----
#
# T-M1-58, PR #172 review (4-й круг, п. 2а): число РЕАЛЬНЫХ LDAP-операций
# на один authenticate() против настоящего тестового openldap не должно
# расти обратно к измеренному дефолту ldap3 (7 операций при get_info=
# SCHEMA, см. authenticate()'s докстринг) - считается через ldap3's
# collect_usage=True, тот же механизм, которым это было измерено. Монки-
# патчим app.core.ldap_client.Connection, а не сам ldap3.Connection -
# подмена видна в точности там, откуда authenticate() на деле берёт имя
# (import Connection в ldap_client.py), не глобально для всего процесса.


@requires_ldap
def test_authenticate_does_not_exceed_the_measured_number_of_real_ldap_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_connection_cls = _ldap_client_module.Connection
    captured: list[Connection] = []

    def _connection_with_usage_tracking(*args: object, **kwargs: object) -> Connection:
        kwargs["collect_usage"] = True
        conn = real_connection_cls(*args, **kwargs)
        captured.append(conn)
        return conn

    monkeypatch.setattr(_ldap_client_module, "Connection", _connection_with_usage_tracking)

    settings = LdapSettingsData(
        server_url=LDAP_TEST_HOST,
        port=LDAP_TEST_PORT,
        base_dn="dc=company,dc=local",
        bind_dn="cn=admin,dc=company,dc=local",
        bind_password="changeme",
        user_match_attribute="mail",
    )

    matched_value = authenticate(
        settings, "ivanov@company.local", "TestPass123!", connect_timeout=5, receive_timeout=5
    )

    assert matched_value == "ivanov@company.local"
    assert len(captured) == 2, "authenticate() должна была открыть ровно два Connection"
    total_ops = sum(conn.usage.operations for conn in captured)
    assert total_ops <= 3, (
        f"authenticate() выполнила {total_ops} реальных LDAP-операций - "
        "измеренный максимум при get_info=LDAP_GET_INFO_NONE - 3 (bind + "
        "search в первом соединении, bind во втором); превышение значит, "
        "что get_info вернулся к дефолту ldap3 (SCHEMA) и снова читает "
        "DSE/схему на каждый bind"
    )
