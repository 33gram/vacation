"""Ограничения `leave_balance_entries` на уровне БД (T-M2-02).

Вставка идёт СЫРЫМ SQL, в обход ORM и сервиса: проверяется CHECK/FK базы,
а не валидация приложения. У каждого отказа проверяется ИМЯ ограничения -
так тест краснеет именно при снятии своего ограничения, а не любого
другого. Контрольные вставки допустимых значений доказывают, что отказ
вызван проверяемым значением.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.leave_balance_entry import LEAVE_BALANCE_ENTRY_TYPES
from tests.modules.balances.helpers import EVENT_DATE, YEAR_END, YEAR_START, Context, make_context

_INSERT = text(
    "INSERT INTO leave_balance_entries (employee_id, leave_type_id, working_year_start, "
    "working_year_end, entry_type, quantity, entry_date, reason, actor_account_id) "
    "VALUES (:employee_id, :leave_type_id, :working_year_start, :working_year_end, "
    ":entry_type, :quantity, :entry_date, :reason, :actor_account_id)"
)


def _row(ctx: Context, **overrides: object) -> dict[str, object]:
    """Допустимая строка типа `accrual`; тест меняет то, что проверяет."""
    return {
        "employee_id": ctx.employee_id,
        "leave_type_id": ctx.leave_type_id,
        "working_year_start": YEAR_START,
        "working_year_end": YEAR_END,
        "entry_type": "accrual",
        "quantity": 28,
        "entry_date": EVENT_DATE,
        "reason": None,
        "actor_account_id": None,
        **overrides,
    }


async def _rejected_by(db_session, row: dict[str, object]) -> str:
    with pytest.raises(IntegrityError) as excinfo:
        await db_session.execute(_INSERT, row)
    return str(excinfo.value)


async def test_valid_system_accrual_is_accepted(db_session) -> None:
    ctx = await make_context(db_session)

    await db_session.execute(_INSERT, _row(ctx))


# --- набор типов и рабочий год --------------------------------------------


async def test_entry_type_outside_the_set_is_rejected(db_session) -> None:
    ctx = await make_context(db_session)

    message = await _rejected_by(db_session, _row(ctx, entry_type="bonus"))

    assert "ck_leave_balance_entries_entry_type" in message


@pytest.mark.parametrize("end_offset_days", [0, -1])
async def test_working_year_must_end_after_it_starts(db_session, end_offset_days: int) -> None:
    import datetime

    ctx = await make_context(db_session)
    end = YEAR_START + datetime.timedelta(days=end_offset_days)

    message = await _rejected_by(db_session, _row(ctx, working_year_end=end))

    assert "ck_leave_balance_entries_working_year" in message


# --- соглашение о знаке: по каждому типу события -----------------------------

_ACTOR_TYPES = {"manual_adjustment", "recalculation", "initial_import", "termination_closeout"}
_REASON_TYPES = {"manual_adjustment", "recalculation"}


_SIGN_REJECTED = [
    ("accrual", 0, "ck_leave_balance_entries_positive_types"),
    ("accrual", -1, "ck_leave_balance_entries_positive_types"),
    ("usage_reversal", 0, "ck_leave_balance_entries_positive_types"),
    ("usage_reversal", -1, "ck_leave_balance_entries_positive_types"),
    ("initial_import", 0, "ck_leave_balance_entries_positive_types"),
    ("initial_import", -5, "ck_leave_balance_entries_positive_types"),
    ("usage", 0, "ck_leave_balance_entries_negative_types"),
    ("usage", 3, "ck_leave_balance_entries_negative_types"),
    ("manual_adjustment", 0, "ck_leave_balance_entries_nonzero_types"),
    ("recalculation", 0, "ck_leave_balance_entries_nonzero_types"),
    ("termination_closeout", 0, "ck_leave_balance_entries_nonzero_types"),
]

_SIGN_ACCEPTED = [
    ("accrual", "28.00"),
    ("usage_reversal", "5.50"),
    ("initial_import", "12"),
    ("usage", "-5.50"),
    ("manual_adjustment", "2.50"),
    ("manual_adjustment", "-2.50"),
    ("recalculation", "1"),
    ("recalculation", "-1"),
    ("termination_closeout", "-14"),
    ("termination_closeout", "3"),
]


def _full_row(ctx: Context, entry_type: str, quantity: object) -> dict[str, object]:
    """Строка типа `entry_type`, у которой выполнено всё, кроме знака."""
    return _row(
        ctx,
        entry_type=entry_type,
        quantity=quantity,
        reason="Причина" if entry_type in _REASON_TYPES else None,
        actor_account_id=ctx.actor_id if entry_type in _ACTOR_TYPES else None,
    )


@pytest.mark.parametrize(("entry_type", "quantity", "constraint"), _SIGN_REJECTED)
async def test_sign_convention_is_enforced_per_entry_type(
    db_session, entry_type: str, quantity: int, constraint: str
) -> None:
    ctx = await make_context(db_session)

    message = await _rejected_by(db_session, _full_row(ctx, entry_type, quantity))

    assert constraint in message


@pytest.mark.parametrize(("entry_type", "quantity"), _SIGN_ACCEPTED)
async def test_each_entry_type_accepts_the_sign_it_allows(
    db_session, entry_type: str, quantity: str
) -> None:
    ctx = await make_context(db_session)

    await db_session.execute(_INSERT, _full_row(ctx, entry_type, quantity))


# --- причина ----------------------------------------------------------------


@pytest.mark.parametrize("entry_type", sorted(_REASON_TYPES))
async def test_reason_is_required_where_the_requirements_demand_it(
    db_session, entry_type: str
) -> None:
    ctx = await make_context(db_session)

    message = await _rejected_by(db_session, {**_full_row(ctx, entry_type, 1), "reason": None})

    assert "ck_leave_balance_entries_reason_required" in message


@pytest.mark.parametrize("entry_type", ["accrual", "manual_adjustment"])
@pytest.mark.parametrize("blank", ["", " ", "   ", "\t", "\n", " \t\n "])
async def test_a_blank_reason_is_not_a_reason_for_any_entry_type(
    db_session, entry_type: str, blank: str
) -> None:
    ctx = await make_context(db_session)
    quantity = 28 if entry_type == "accrual" else 1

    message = await _rejected_by(
        db_session, {**_full_row(ctx, entry_type, quantity), "reason": blank}
    )

    assert "ck_leave_balance_entries_reason_not_blank" in message


@pytest.mark.parametrize("entry_type", ["accrual", "usage", "usage_reversal"])
async def test_reason_may_be_absent_for_system_events(db_session, entry_type: str) -> None:
    ctx = await make_context(db_session)
    quantity = -1 if entry_type == "usage" else 1

    await db_session.execute(_INSERT, _full_row(ctx, entry_type, quantity))


# --- автор ------------------------------------------------------------------


@pytest.mark.parametrize("entry_type", sorted(_ACTOR_TYPES))
async def test_actor_is_required_for_events_made_by_a_person(db_session, entry_type: str) -> None:
    ctx = await make_context(db_session)
    row = {**_full_row(ctx, entry_type, 1), "actor_account_id": None}

    message = await _rejected_by(db_session, row)

    assert "ck_leave_balance_entries_actor_required" in message


@pytest.mark.parametrize("entry_type", ["accrual", "usage", "usage_reversal"])
async def test_actor_may_be_absent_for_system_events(db_session, entry_type: str) -> None:
    ctx = await make_context(db_session)
    quantity = -1 if entry_type == "usage" else 1

    await db_session.execute(
        _INSERT, {**_full_row(ctx, entry_type, quantity), "actor_account_id": None}
    )


# --- ссылки -----------------------------------------------------------------


@pytest.mark.parametrize("column", ["employee_id", "leave_type_id", "actor_account_id"])
async def test_references_must_point_at_existing_rows(db_session, column: str) -> None:
    ctx = await make_context(db_session)
    row = _full_row(ctx, "manual_adjustment", 1)

    message = await _rejected_by(db_session, {**row, column: 2_000_000_000})

    assert "leave_balance_entries_" in message and "fkey" in message


def test_every_entry_type_of_the_model_has_a_rejected_and_an_accepted_sign_case() -> None:
    """Перечень типов берётся из модели: восьмой тип без случаев знака
    краснит этот тест (PR #177 review, Б1) - иначе соглашение о знаке
    остаётся непроверенным для нового типа."""
    expected = set(LEAVE_BALANCE_ENTRY_TYPES)

    assert {case[0] for case in _SIGN_REJECTED} == expected
    assert {case[0] for case in _SIGN_ACCEPTED} == expected
