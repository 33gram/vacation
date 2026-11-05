import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.health import router as health_router
from app.core.logging import configure_logging
from app.core.security import warm_dummy_password_hash_cache
from app.core.validation_texts import russian_message
from app.db.session import get_session_factory
from app.modules.admin.business_rules_router import router as business_rules_router
from app.modules.admin.ldap_settings_router import router as ldap_settings_router
from app.modules.admin.production_calendar_router import router as production_calendar_router
from app.modules.auth.bootstrap import ensure_bootstrap_admin
from app.modules.auth.exceptions import (
    AccessDeniedError,
    AuthenticationError,
    LdapNotConfiguredError,
)
from app.modules.auth.password_policy import WeakPasswordError
from app.modules.auth.router import router as auth_router
from app.modules.balances.router import router as balances_router
from app.modules.business_rules.service import validate_all as validate_business_rules
from app.modules.employees.department_router import router as department_router
from app.modules.employees.employee_router import router as employee_router
from app.modules.employees.import_parsing import ImportFileError
from app.modules.employees.unique_violations import unique_violation_field
from app.modules.leave_types.router import router as leave_types_router
from app.schemas.errors import PasswordPolicyViolationOut

logger = logging.getLogger(__name__)

configure_logging()

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # FR-AUTH-002/14 (T-M1-41, PR #165 review): прогреть кэш фиктивного
    # хэша ДО обслуживания первого запроса — иначе первый вызов
    # verify_against_dummy_hash в процессе платит дважды (строит хэш и
    # тут же сверяет с ним) и отличим от обычного «неверный пароль» по
    # времени ответа почти вдвое, см. core/security.py.
    warm_dummy_password_hash_cache()
    async with get_session_factory()() as session:
        # T-M2-09: все обязательные параметры `business_rules_config`
        # существуют и разбираются — иначе отказ старта с перечнем ВСЕХ
        # проблем, а не по одному падению на запрос (см. сервис).
        await validate_business_rules(session)
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
app.include_router(balances_router)
app.include_router(department_router)
app.include_router(employee_router)
app.include_router(ldap_settings_router)
app.include_router(leave_types_router)
app.include_router(production_calendar_router)
app.include_router(business_rules_router)


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


@app.exception_handler(WeakPasswordError)
async def _handle_weak_password_error(_: Request, exc: WeakPasswordError) -> JSONResponse:
    """Сетка уровня приложения (PR #143 review, коммит 1), симметрично
    `IntegrityError`/`ImportFileError` ниже: до этой правки три вызывающих
    места (`auth/router.change_password`, `employee_router.create_
    employee`/`update_employee`) строили ЭТОТ ЖЕ ответ локальным `try/
    except`, дублируя друг друга — и место, которое забыло бы обернуть
    `validate_password_strength`/`_apply_auth_type_change`, отдало бы
    500 вместо 400. Локальные блоки убраны; здесь — единственное место,
    которое строит ответ."""
    return JSONResponse(
        status_code=400,
        content={
            "detail": [
                PasswordPolicyViolationOut(code=v.code, message=v.message).model_dump()
                for v in exc.violations
            ]
        },
    )


@app.exception_handler(ImportFileError)
async def _handle_import_file_error(_: Request, exc: ImportFileError) -> JSONResponse:
    # T-M1-11: тот же текст, что раньше давал локальный `try/except` в
    # `employee_router.py`, — сообщение уже безопасно (собственный текст
    # модуля разбора/записи, не текст исключения БД), поэтому `str(exc)`
    # здесь можно передавать как есть. Сетка на случай, если раздобор
    # или запись поднимут `ImportFileError` из места, которое не
    # обернули локально (PR #139 review, коммит 1).
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(RequestValidationError)
async def _handle_request_validation_error(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    """`T-M1-40`, `NFR-SEC-004` (и `NFR-SEC-001`/`NFR-SEC-002` для
    персональных данных): собственный обработчик FastAPI для этого
    исключения (`fastapi.exception_handlers.
    request_validation_exception_handler`) возвращает
    `jsonable_encoder(exc.errors())` как есть, а `exc.errors()` (pydantic
    v2) кладёт ПРИСЛАННОЕ ЗНАЧЕНИЕ в `input` каждой ошибки, а для части
    валидаторов — ещё и в `ctx`. Проверено прогоном на поднятом
    инстансе, а не рассуждением о pydantic: пароль длиннее
    `MAX_PASSWORD_LENGTH_CHARS` возвращается в `input` как есть; то же
    самое — с нестроковым `bind_password` (пароль служебной записи LDAP,
    `LdapSettingsIn`/`LdapTestRequest` — там `max_length` нет вовсе, утечка
    не привязана именно к этому ограничению); неверный тип `full_name`/
    `email` кладёт присланное значение в `input` точно так же
    (персональные данные, не пароль — `NFR-SEC-001`/`NFR-SEC-002`).

    Тело пересобирается БЕЛЫМ СПИСКОМ из трёх полей (`loc`/`msg`/`type`),
    а не удалением `input`/`ctx` из исходного словаря: белый список
    остаётся безопасным, даже если будущая версия pydantic/FastAPI
    добавит ещё один ключ, в который попадёт присланное значение, — чёрный
    список такой ключ пропустил бы молча. Это же совпадает с уже
    опубликованным контрактом OpenAPI: схема `ValidationError` в
    сгенерированной спецификации всегда объявляла только `loc`/`msg`/
    `type` (встроенная в FastAPI модель документации `input`/`ctx` не
    включает никогда) — то есть прежнее поведение уже расходилось со
    схемой, и дело не только в утечке секретов, но и в недокументированных
    полях сверх контракта.

    `ctx` вычищается наравне с `input` — проверено прогоном на каждом виде
    валидатора, который сегодня встречается в схемах (превышение длины,
    неверный тип, неверный `Literal`, неверная дата): везде только
    диагностические числа/строки — `{"max_length": 128, "actual_length":
    210, "field_type": "Value"}` для слишком длинного пароля, `{"expected":
    "'standard' or 'shift'"}` для неверного перечисления, `{"error":
    "invalid character in year"}` для неверной даты, — присланного значения
    в `ctx` не оказалось ни разу. Вычищается всё равно, тем же белым
    списком, а не оставляется как «проверено безопасным»: валидатор поля,
    которое прогон не затронул, может положить значение в `ctx` и в
    будущем, и белый список защищает от этого без необходимости перепроверять.

    `msg` переводится на русский по `type` (`T-M2-28`, `NFR-LOC-001`, `ui.md` §7 запрещает
    переписывать серверный текст на клиенте): карта текстов — одно место,
    `app/core/validation_texts.py`, неизвестный `type` даёт русский запасной текст. Присланное
    значение в `msg` не попадает (там же, как и почему).

    ГРАНИЦА белого списка (`T-M2-28`, правило 8): список намеренно сохраняет `loc`, а у
    вложенного тела последний элемент `loc` — КЛЮЧ ИЗ ЗАПРОСА КЛИЕНТА (`["body", "values",
    "<ключ>"]` у параметров бизнес-правил). Утечки нет: свой ввод возвращается своему же
    клиенту, а без ключа ошибку не положить под поле формы. Но это граница списка, заведённого
    ради `NFR-SEC-004`: присланная строка в ответе есть, и она только в `loc`, не в `msg`.

    Код ответа остаётся 422, форма — `{"detail": [...]}`: это ровно то, что
    уже описывает `openapi-export`/сгенерированный клиент, а не новый
    контракт.

    Из присланного тела в этом обработчике ничего не логируется — утечка в
    лог та же утечка (`NFR-SEC-004`)."""
    sanitized_errors = [
        {"loc": error.get("loc"), "msg": russian_message(error), "type": error.get("type")}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(sanitized_errors)})


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
        content={"detail": "Конфликт с уже существующими данными. Попробуйте ещё раз."},
    )
