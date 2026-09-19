from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.health import router as health_router
from app.core.logging import configure_logging
from app.db.session import get_session_factory
from app.modules.admin.ldap_settings_router import router as ldap_settings_router
from app.modules.admin.production_calendar_router import router as production_calendar_router
from app.modules.auth.bootstrap import ensure_bootstrap_admin
from app.modules.auth.exceptions import (
    AccessDeniedError,
    AuthenticationError,
    LdapNotConfiguredError,
)
from app.modules.auth.router import router as auth_router
from app.modules.employees.department_router import router as department_router
from app.modules.employees.employee_router import router as employee_router

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
app.include_router(auth_router)
app.include_router(department_router)
app.include_router(employee_router)
app.include_router(ldap_settings_router)
app.include_router(production_calendar_router)


@app.exception_handler(AuthenticationError)
async def _handle_authentication_error(_: Request, __: AuthenticationError) -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": "Неверный логин или пароль"})


@app.exception_handler(AccessDeniedError)
async def _handle_access_denied_error(_: Request, __: AccessDeniedError) -> JSONResponse:
    # FR-AUTH-004: единый текст для отсутствующей/деактивированной карточки
    # сотрудника и отключённой учётной записи.
    return JSONResponse(
        status_code=403,
        content={"detail": "Доступ запрещён. Обратитесь к HR-администратору."},
    )


@app.exception_handler(LdapNotConfiguredError)
async def _handle_ldap_not_configured_error(_: Request, __: LdapNotConfiguredError) -> JSONResponse:
    # Не должно происходить через /api/auth/login (он сам проверяет режим
    # внедрения), но остаётся защитой на случай гонки между проверкой и
    # обращением к каталогу.
    return JSONResponse(status_code=401, content={"detail": "Неверный логин или пароль"})
