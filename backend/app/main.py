from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.health import router as health_router
from app.core.logging import configure_logging
from app.db.session import get_session_factory
from app.modules.auth.bootstrap import ensure_bootstrap_admin

configure_logging()

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async with get_session_factory()() as session:
        await ensure_bootstrap_admin(session)
    yield


app = FastAPI(title="Система учёта отпусков — API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
