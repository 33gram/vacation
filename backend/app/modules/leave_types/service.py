"""Справочник видов отсутствий: чтение и ведение (T-M2-10, `FR-ADMIN-001`).

Удаления нет сознательно: вид выводят из обращения признаком `is_active` (A-3.1) - на
него могут ссылаться события журнала и заявки.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leave_type import LeaveType
from app.modules.balances.service import MAIN_LEAVE_TYPE_CODE, leave_type_has_events
from app.schemas.leave_type import LeaveTypeCreate, LeaveTypeUpdate


class LeaveTypeNotFoundError(Exception):
    pass


class LeaveTypeCodeConflictError(Exception):
    """Вид с таким кодом уже есть."""


class LeaveTypeCodeImmutableError(ValueError):
    """Код вида после создания не меняется."""


class MainLeaveTypeProtectedError(ValueError):
    """Основной вид нельзя вывести из обращения или лишить признака «ведётся остаток»."""


class LeaveTypeHasEventsError(Exception):
    """Нельзя снять «по виду ведётся остаток» у вида, по которому есть события.
    Проверка моментальная, замка на писателях журнала нет: если первое событие по виду
    записано одновременно с правкой, признак окажется снятым при живом событии. Цена
    такой гонки мала: `GET .../balances` показывает вид по наличию событий и без
    признака (история не прячется), начисление по виду без признака не идёт (оно только
    по основному виду, а он от этой правки защищён); расходится лишь справочник и журнал,
    правится повторным включением признака."""


async def list_leave_types(session: AsyncSession) -> list[LeaveType]:
    result = await session.execute(select(LeaveType).order_by(LeaveType.id))
    return list(result.scalars())


async def create_leave_type(session: AsyncSession, payload: LeaveTypeCreate) -> LeaveType:
    if (
        await session.scalar(select(LeaveType.id).where(LeaveType.code == payload.code))
    ) is not None:
        raise LeaveTypeCodeConflictError(f"Вид отпуска с кодом {payload.code!r} уже существует")
    leave_type = LeaveType(**payload.model_dump())
    session.add(leave_type)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Одновременное создание того же кода: предпроверка выше его не видит.
        await session.rollback()
        raise LeaveTypeCodeConflictError(
            f"Вид отпуска с кодом {payload.code!r} уже существует"
        ) from exc
    await session.commit()
    return leave_type


async def update_leave_type(
    session: AsyncSession, leave_type_id: int, payload: LeaveTypeUpdate
) -> LeaveType:
    # Строка вида блокируется на время проверки и записи: две одновременные правки одного
    # вида не перемешивают атрибуты.
    leave_type = await session.get(LeaveType, leave_type_id, with_for_update=True)
    if leave_type is None:
        raise LeaveTypeNotFoundError("Вид отпуска не найден")
    if payload.code is not None and payload.code != leave_type.code:
        raise LeaveTypeCodeImmutableError(
            "Код вида отпуска после создания не меняется: по нему находится основной вид, "
            "переименование сломало бы начисление. Меняется наименование"
        )
    if leave_type.code == MAIN_LEAVE_TYPE_CODE and not (
        payload.balance_tracked and payload.is_active
    ):
        # Независимо от событий: на свежей базе их ещё нет, а начисление (`FR-BAL-001`)
        # находит вид по КОДУ и писало бы события по виду, который справочник объявил
        # выведенным из обращения или не ведущим остаток, - `GET .../balances` его бы
        # не показал при живом журнале.
        raise MainLeaveTypeProtectedError(
            "Основной оплачиваемый отпуск нельзя деактивировать и нельзя лишить признака "
            "«по виду ведётся остаток»: от него зависит начисление (FR-BAL-001)"
        )
    if leave_type.balance_tracked and not payload.balance_tracked:
        if await leave_type_has_events(session, leave_type_id):
            raise LeaveTypeHasEventsError(
                "Нельзя снять признак «по виду ведётся остаток»: по виду уже есть события "
                "журнала, показ остатка разошёлся бы с записанной историей. "
                "Вид можно вывести из обращения признаком активности"
            )
    for field, value in payload.model_dump(exclude={"code"}).items():
        setattr(leave_type, field, value)
    await session.commit()
    return leave_type
