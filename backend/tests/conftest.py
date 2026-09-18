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

_forbidden_skips: list[pytest.TestReport] = []


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Собирает пропущенные тесты для `pytest_sessionfinish` (PR #137
    review, узкое ревью): точечная защита конкретно LDAP-тестов
    (`tests/ldap_guard.py`) не ловит пропуск теста, который стучится
    в LDAP по-своему и её не импортирует. Здесь — инвариант уровнем выше,
    общий для любого пропуска в любом файле, независимо от причины.

    `xfail` тоже приходит сюда с `report.skipped=True` — исключаем его
    через `report.wasxfail` (см. `_pytest/skipping.py`), иначе первый же
    ожидаемый xfail завалил бы прогон как «неожиданный пропуск»."""
    if report.skipped and not hasattr(report, "wasxfail"):
        _forbidden_skips.append(report)


def _skip_reason(report: pytest.TestReport) -> str:
    if isinstance(report.longrepr, tuple) and len(report.longrepr) == 3:
        return str(report.longrepr[2])
    return str(report.longrepr)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """С `PYTEST_FORBID_SKIPS=1` (выставлено в CI) любой пропущенный тест
    заваливает прогон с ненулевым кодом возврата -- это осознанно жёстче,
    чем локальная работа: без docker compose (LDAP, например, недоступен)
    `make test-backend` должен по-прежнему уметь пропускать такие тесты,
    поэтому без этой переменной пропуски остаются разрешены."""
    if os.environ.get("PYTEST_FORBID_SKIPS") != "1" or not _forbidden_skips:
        return

    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line("")
        reporter.write_line(
            f"PYTEST_FORBID_SKIPS=1: {len(_forbidden_skips)} тест(ов) пропущено "
            "— в CI это запрещено:",
            red=True,
        )
        for report in _forbidden_skips:
            reporter.write_line(f"  - {report.nodeid}: {_skip_reason(report)}", red=True)

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
