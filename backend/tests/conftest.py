"""Конфигурация тестов backend и защита от молчаливого пропуска в CI
(PR #137 review, круг 3, замечание #2).

Границу защиты (`PYTEST_FORBID_SKIPS=1`, см. `pytest_sessionfinish` ниже)
нужно формулировать через собственный учёт pytest, а не через список
случаев, — список уже дважды оказывался неполным. Механизм заваливает
прогон ровно на том, что pytest сам считает пропуском, — включая пропуск
на стадии сбора модуля (`pytest.skip(..., allow_module_level=True)`,
`pytest.importorskip(...)`), не считая xfail. Он НЕ ловит исключение
файла из сбора (`collect_ignore`, имя вне маски `test_*.py`, флаги
`--ignore`/`-k`/`-m`) — pytest такие файлы пропусками не считает, и в
отличие от пропуска это всегда видимая правка в диффе.

Проверка по атрибуту `skipped` в `--junitxml` НЕ эквивалентна: junit-xml
засчитывает xfail как skipped (проверено: 3 skipped + 1 xfailed у pytest
против `skipped="4"` в отчёте), поэтому такая проверка ложно срабатывала
бы на каждом xfail. Поэтому пропуски считаются напрямую через хуки
`pytest_runtest_logreport`/`pytest_collectreport`, а не через `--junitxml`.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db_session, get_engine
from app.main import app

_forbidden_skips: list[pytest.TestReport | pytest.CollectReport] = []


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Собирает пропуски, случившиеся на стадии выполнения теста
    (`runtest`) — см. границу механизма в докстринге модуля выше.

    `xfail` тоже приходит сюда с `report.skipped=True` — исключаем его
    через `report.wasxfail` (см. `_pytest/skipping.py`), иначе первый же
    ожидаемый xfail завалил бы прогон как «неожиданный пропуск»."""
    if report.skipped and not hasattr(report, "wasxfail"):
        _forbidden_skips.append(report)


def pytest_collectreport(report: pytest.CollectReport) -> None:
    """Собирает пропуски, случившиеся на стадии СБОРА (`collect`), —
    см. границу механизма в докстринге модуля выше. Без этого хука
    `pytest.skip(..., allow_module_level=True)` и
    `pytest.importorskip(...)` проходили бы CI молча: элемент теста не
    создаётся, `pytest_runtest_logreport` о таком пропуске не узнаёт.

    `wasxfail` здесь не бывает — xfail — исход выполнения теста, а не
    сбора, поэтому фильтровать его не нужно."""
    if report.outcome == "skipped":
        _forbidden_skips.append(report)


def _skip_nodeid(report: pytest.TestReport | pytest.CollectReport) -> str:
    return report.nodeid or "<сбор корня сессии>"


def _skip_reason(report: pytest.TestReport | pytest.CollectReport) -> str:
    if isinstance(report.longrepr, tuple) and len(report.longrepr) == 3:
        return str(report.longrepr[2])
    return str(report.longrepr)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """С `PYTEST_FORBID_SKIPS=1` (выставлено в CI) любой пропуск,
    собранный выше (тест или модуль), заваливает прогон с ненулевым
    кодом возврата — граница того, что здесь считается пропуском,
    описана в докстринге модуля. Это осознанно жёстче, чем локальная
    работа: без docker compose (LDAP, например, недоступен)
    `make test-backend` должен по-прежнему уметь пропускать такие тесты,
    поэтому без этой переменной пропуски остаются разрешены."""
    if os.environ.get("PYTEST_FORBID_SKIPS") != "1" or not _forbidden_skips:
        return

    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line("")
        reporter.write_line(
            f"PYTEST_FORBID_SKIPS=1: {len(_forbidden_skips)} пропуск(ов) "
            "— в CI это запрещено:",
            red=True,
        )
        for report in _forbidden_skips:
            reporter.write_line(f"  - {_skip_nodeid(report)}: {_skip_reason(report)}", red=True)

    session.exitstatus = 1


@pytest.fixture(scope="session", autouse=True)
def _require_test_database() -> None:
    """T-M1-31, PR #136, замечание 2 оси спецификации: защищает инвариант
    «тесты не работают на не-тестовой базе», а не одну точку входа.
    `make test-backend` переопределяет `POSTGRES_DB`, но прямой
    `docker compose exec backend pytest` о такой защите не знает и раньше
    молча шёл в dev-базу. `database_url` разбирается через
    `sqlalchemy.engine.make_url` — он корректен и для собранного из частей
    URL, и для полного override (см. `app/core/config.py`).
    """
    db_name = make_url(get_settings().database_url).database
    if db_name is None or not db_name.endswith("_test"):
        pytest.exit(
            f"Отказ: тесты пытаются подключиться к базе {db_name!r}, "
            "не похожей на тестовую (имя должно оканчиваться на '_test', "
            "как vacation_tracker_test у make test-backend и в CI). "
            "См. README.md, раздел «Тестирование».",
            returncode=1,
        )


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    # app.db.session.get_engine() кэширует движок (и его пул соединений
    # asyncpg) на уровне модуля; asyncpg-соединение нельзя переиспользовать
    # на другом event loop. Без общего на всю сессию loop pytest-asyncio
    # создавал бы новый loop на каждый тест и падал с "attached to a
    # different loop" на втором тесте, использующем db_session/приложение.
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def db_session() -> AsyncSession:
    """Сессия на отдельном соединении в незакоммиченной транзакции: код
    сервисов свободно вызывает `session.commit()` (внутри он становится
    релизом SAVEPOINT благодаря `join_transaction_mode`), а по завершении
    теста внешняя транзакция откатывается — БД не засоряется между тестами.
    """
    engine = get_engine()
    async with engine.connect() as connection:
        await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await connection.rollback()


@pytest.fixture
async def api_client(db_session: AsyncSession) -> AsyncClient:
    """HTTP-клиент для тестов эндпоинтов (T-M1-13/16/17): подменяет
    `get_db_session` приложения на `db_session` этого теста, чтобы записи,
    созданные через HTTP-запросы, тоже откатывались по завершении теста, а
    не коммитились в реальную БД мимо фикстуры изоляции."""

    async def _override_get_db_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = _override_get_db_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_db_session, None)
