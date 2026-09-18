import asyncio
from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db_session, get_engine
from app.main import app


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
