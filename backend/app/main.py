import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

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
from app.modules.employees.import_parsing import ImportFileError
from app.modules.employees.unique_violations import unique_violation_field

logger = logging.getLogger(__name__)

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


@app.exception_handler(ImportFileError)
async def _handle_import_file_error(_: Request, exc: ImportFileError) -> JSONResponse:
    # T-M1-11: тот же текст, что раньше давал локальный `try/except` в
    # `employee_router.py`, — сообщение уже безопасно (собственный текст
    # модуля разбора/записи, не текст исключения БД), поэтому `str(exc)`
    # здесь можно передавать как есть. Сетка на случай, если раздобор
    # или запись поднимут `ImportFileError` из места, которое не
    # обернули локально (PR #139 review, коммит 1).
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(IntegrityError)
async def _handle_unexpected_integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
    """Сетка уровня приложения (PR #139 review, коммит 1), а не основная
    защита: каждый путь записи в `employees`/`accounts` по-прежнему
    обязан сам ловить `IntegrityError` и переводить её через
    `unique_violations` в отказ с указанием поля — это даёт лучшее
    сообщение, чем здесь возможно (там известно, какое значение из
    ТЕЛА ЗАПРОСА привело к конфликту, здесь — только имя нарушенного
    ограничения). Этот обработчик — на случай, если такой путь забудет
    это сделать (как ранее `import_service` при исчерпании повторов) или
    столкнётся с ограничением вне карты `unique_violations`: без него
    клиент получил бы 500, а лог — трассировку asyncpg, чей `str()`
    содержит DETAIL с реальным значением поля (NFR-SEC-001/002).

    Поэтому `str(exc)` не попадает НИКУДА — ни в ответ, ни в лог; только
    имя поля, если оно известно `unique_violations` (взято по имени
    ограничения, не разбором текста), и путь запроса."""
    field = unique_violation_field(exc)
    logger.error(
        "Unhandled IntegrityError reached the app-level safety net (path=%s, field=%s)",
        request.url.path,
        field or "unknown",
    )
    return JSONResponse(
        status_code=409,
        content={"detail": "The request conflicts with existing data. Please retry."},
    )
