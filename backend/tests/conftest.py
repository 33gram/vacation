import asyncio
from collections.abc import Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_engine
from app.main import app


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
