"""`/api/employees*` (T-M1-15, FR-EMP-001 в редакции v1.7).

Ведение (`POST`/`PUT`) — `hr_admin`/`system_admin`. Просмотр — любой
аутентифицированный, но что именно видно, решает
`employee_service.can_view_employee`/`list_visible_employees`, а не
`require_roles` (своя карточка, своя команда, либо привилегированная
роль — `plan.md` §5.1).

`POST /import` (T-M1-11): приложение само не пишет загруженный файл ни
на диск, ни в БД (`plan.md` §6, то же решение T-000, что и для вложений
к заявкам) — код читает его в память и работает с байтами напрямую. Это
не гарантия, что файл нигде не касается диска: Starlette выгружает
часть multipart-запроса больше 1 МиБ во временный файл ДО того, как
код приложения вообще получает управление (`starlette.formparsers.
MultiPartParser`, удаляется по завершении запроса) — из кода приложения
это не предотвратить, см. `plan.md` §6, п. 5, `NFR-COMPLY-001`. В лог
попадают только количества и номера строк; `ImportSummary.errors[].
reason` может содержать значения полей (ФИО, e-mail, табельный номер) —
это допустимо в ОТВЕТЕ (его видит загрузивший HR-администратор,
которому эти данные и так доступны), но не в логе (`NFR-SEC-001/002`)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.account import Account
from app.modules.auth.password_policy import WeakPasswordError
from app.modules.auth.rbac import get_current_account, require_roles
from app.modules.employees import employee_service
from app.modules.employees.import_parsing import ImportFileError, parse_import_file
from app.modules.employees.import_service import import_employees
from app.schemas.employee import (
    EmployeeCreate,
    EmployeeDeactivate,
    EmployeeOut,
    EmployeeUpdate,
    ImportRowError,
    ImportSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/employees", tags=["employees"])

_require_employee_admin = require_roles("hr_admin", "system_admin")


@router.get("", response_model=list[EmployeeOut])
async def get_employees(
    include_terminated: bool = False,
    session: AsyncSession = Depends(get_db_session),
    account: Account = Depends(get_current_account),
) -> list[EmployeeOut]:
    employees = await employee_service.list_visible_employees(
        session, account, include_terminated=include_terminated
    )
    return [EmployeeOut.model_validate(e) for e in employees]


@router.post("", response_model=EmployeeOut, status_code=status.HTTP_201_CREATED)
async def create_employee(
    payload: EmployeeCreate,
    session: AsyncSession = Depends(get_db_session),
    _: Account = Depends(_require_employee_admin),
) -> EmployeeOut:
    try:
        employee = await employee_service.create_employee(session, payload)
    except WeakPasswordError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return EmployeeOut.model_validate(employee)


@router.post("/import", response_model=ImportSummary)
async def import_employees_endpoint(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
    _: Account = Depends(_require_employee_admin),
) -> ImportSummary:
    content = await file.read()
    try:
        rows = parse_import_file(file.filename or "", content)
        result = await import_employees(session, rows)
    except ImportFileError as exc:
        # PR #139 review, коммит 1, п. 4: раньше `try` покрывал только
        # разбор файла — исчерпание повторов записи (`import_employees`)
        # поднимает тот же `ImportFileError`, но снаружи блока, поэтому
        # доходило до клиента как 500. Локальный отлов здесь и
        # `main.py`'s обработчик того же типа — оба дают один и тот же
        # ответ; локальный явно называет намерение на месте вызова,
        # `main.py` — сетка на путь, который его забудет.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # NFR-SEC-001/002: ни одно значение поля файла (ФИО, e-mail,
    # табельный номер) сюда не попадает — только количества и номера
    # строк, отсутствие/причина ошибки не логируется по значению.
    logger.info(
        "Employee import finished: accepted=%d rejected=%d error_rows=%s",
        result.accepted_count,
        result.rejected_count,
        sorted({error.row_number for error in result.errors}),
    )

    return ImportSummary(
        accepted_count=result.accepted_count,
        rejected_count=result.rejected_count,
        errors=[
            ImportRowError(row_number=e.row_number, field=e.field, reason=e.reason)
            for e in result.errors
        ],
    )


@router.get("/{employee_id}", response_model=EmployeeOut)
async def get_employee(
    employee_id: int,
    session: AsyncSession = Depends(get_db_session),
    account: Account = Depends(get_current_account),
) -> EmployeeOut:
    employee = await employee_service.get_employee(session, employee_id)
    if employee is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found")
    if not await employee_service.can_view_employee(session, account, employee_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
    return EmployeeOut.model_validate(employee)


@router.put("/{employee_id}", response_model=EmployeeOut)
async def update_employee(
    employee_id: int,
    payload: EmployeeUpdate,
    session: AsyncSession = Depends(get_db_session),
    _: Account = Depends(_require_employee_admin),
) -> EmployeeOut:
    try:
        employee = await employee_service.update_employee(session, employee_id, payload)
    except employee_service.EmployeeNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return EmployeeOut.model_validate(employee)


@router.post("/{employee_id}/deactivate", response_model=EmployeeOut)
async def deactivate_employee(
    employee_id: int,
    payload: EmployeeDeactivate,
    session: AsyncSession = Depends(get_db_session),
    _: Account = Depends(_require_employee_admin),
) -> EmployeeOut:
    try:
        employee = await employee_service.deactivate_employee(session, employee_id, payload)
    except employee_service.EmployeeNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return EmployeeOut.model_validate(employee)
