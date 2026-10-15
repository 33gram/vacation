"""Real-socket fixtures reproducing the two ways an LDAP directory can
hang a caller (T-M1-58) - shared between `tests/core/test_ldap_client.py`
and the admin router's end-to-end test, by the same no-`test_`-prefix
convention as `tests/fake_integrity_error.py` (importable, never
collected as a test module itself).

Both are self-contained (localhost only, no firewall rules, no external
network dependency, no elevated capabilities) so they reproduce
identically in the dev container and in CI's bare `ubuntu-latest`
runner (`.github/workflows/ci.yml`'s `backend-tests` job runs pytest
directly on the runner, not inside Docker) - an approach that depended
on a specific external address being silently black-holed (a
documented-reserved IP range, say) would not have this guarantee, and
was tried and dropped for exactly that reason before landing on the
techniques below. See `tests/core/test_ldap_client.py`'s module
docstring for the full account of what each one reproduces and why two
are needed, not one.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def black_hole_server() -> Iterator[int]:
    """Слушает, принимает РОВНО одно соединение, никогда не отвечает и
    не закрывает - воспроизводит каталог, который ДОСТИЖИМ (TCP-connect
    проходит мгновенно), но не отвечает. Однопоточный (один `accept()`,
    без цикла) - этого достаточно для одного вызова `authenticate`/
    `test_connection` на тест, и не создаёт лишних потоков, которые
    запутали бы замер числа потоков в `tests/core/test_ldap_client.py`."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)
    accepted: list[socket.socket] = []

    def _accept_once() -> None:
        try:
            conn, _ = srv.accept()
            accepted.append(conn)
        except OSError:
            pass

    thread = threading.Thread(target=_accept_once, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        srv.close()
        thread.join(timeout=1)
        for conn in accepted:
            conn.close()


@contextmanager
def hanging_connect_target() -> Iterator[int]:
    """Слушатель с `backlog=0`, предзаполненный одним "наполнителем" -
    любое ДАЛЬНЕЙШЕЕ входящее соединение попадает в переполненную
    очередь и молча отбрасывается ядром (Linux, `tcp_abort_on_overflow`
    по умолчанию 0 - не RST, тихое отбрасывание SYN), с точки зрения
    клиента неотличимо от потерянного в сети пакета. Воспроизводит
    зависание самого `connect()`, не ожидание ответа после него."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(0)
    filler = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    filler.connect(("127.0.0.1", port))
    try:
        yield port
    finally:
        filler.close()
        srv.close()
