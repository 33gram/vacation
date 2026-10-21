"""Остаток как сумма событий, ручная корректировка и история
(T-M2-08, `FR-BAL-002`, `FR-BAL-004`).

Ожидаемые числа посчитаны в тесте ЯВНО, независимо от `get_balance`.
Критерии `FR-BAL-001` и `FR-BAL-003` относятся к `T-M2-05`/`T-M2-07`,
`FR-BAL-002` как эндпоинт и экран - к `T-M2-11`/`T-M2-13`; здесь они не
проверяются, проверяется только то, что журнал позволяет их выполнить.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.leave_balance_entry import (
    ACTOR_REQUIRED_ENTRY_TYPES,
    LEAVE_BALANCE_ENTRY_TYPES,
    REASON_REQUIRED_ENTRY_TYPES,
    LeaveBalanceEntry,
)
from app.modules.balances.service import (
    BalanceEntryError,
    adjust_balance,
    get_balance,
    list_entries,
)
from tests.modules.balances.helpers import EVENT_DATE, YEAR_END, YEAR_START, Context, make_context


def _event(ctx: Context, entry_type: str, quantity: str, **overrides: object) -> LeaveBalanceEntry:
    start = overrides.pop("working_year_start", YEAR_START)
    return LeaveBalanceEntry(
        employee_id=overrides.pop("employee_id", ctx.employee_id),
        leave_type_id=overrides.pop("leave_type_id", ctx.leave_type_id),
        working_year_start=start,
        working_year_end=start.replace(year=start.year + 1),
        entry_type=entry_type,
        quantity=Decimal(quantity),
        entry_date=overrides.pop("entry_date", EVENT_DATE),
        **overrides,
    )


async def _adjust(db_session, ctx: Context, quantity: str, reason: str, actor_id: int, **kw):
    return await adjust_balance(
        db_session,
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        working_year_start=YEAR_START,
        working_year_end=YEAR_END,
        quantity=Decimal(quantity),
        reason=reason,
        actor_account_id=actor_id,
        entry_date=kw.get("entry_date", EVENT_DATE),
    )


async def _count(db_session) -> int:
    return (await db_session.execute(select(func.count(LeaveBalanceEntry.id)))).scalar_one()


# --- остаток - сумма событий --------------------------------------------------


async def test_balance_without_events_is_zero(db_session) -> None:
    ctx = await make_context(db_session)

    assert await get_balance(db_session, ctx.employee_id, ctx.leave_type_id, YEAR_START) == 0


async def test_balance_is_the_sum_of_signed_events_of_different_types(db_session) -> None:
    ctx = await make_context(db_session)
    db_session.add_all(
        [
            _event(ctx, "accrual", "28.00"),
            _event(ctx, "usage", "-5.50"),
        ]
    )
    await db_session.flush()
    await _adjust(db_session, ctx, "2.50", "Перенос из прошлой системы", ctx.actor_id)
    new_balance = await _adjust(db_session, ctx, "-1", "Описка в переносе", ctx.other_actor_id)

    # 28 - 5.5 + 2.5 - 1, посчитано здесь, а не функцией, которую проверяем.
    assert new_balance == Decimal("24.00")
    assert await get_balance(db_session, ctx.employee_id, ctx.leave_type_id, YEAR_START) == Decimal(
        "24.00"
    )


# Сценарий суммы: по одному событию КАЖДОГО типа модели. Знак - в самом
# количестве, итог посчитан здесь явно: 28 - 5 + 2 - 1.5 + 3 + 10 - 4.
_ALL_TYPES_SCENARIO = [
    ("accrual", "28.00"),
    ("usage", "-5.00"),
    ("usage_reversal", "2.00"),
    ("manual_adjustment", "-1.50"),
    ("recalculation", "3.00"),
    ("initial_import", "10.00"),
    ("termination_closeout", "-4.00"),
]
_ALL_TYPES_EXPECTED = Decimal("32.50")


def test_the_sum_scenario_covers_every_entry_type_of_the_model() -> None:
    """Перечень типов - из модели: восьмой тип без события в сценарии краснит
    этот тест (PR #177 review, Б1), а не остаётся вне суммы."""
    assert {entry_type for entry_type, _ in _ALL_TYPES_SCENARIO} == set(LEAVE_BALANCE_ENTRY_TYPES)
    assert len(_ALL_TYPES_SCENARIO) == len(LEAVE_BALANCE_ENTRY_TYPES)


async def test_balance_sums_one_event_of_every_entry_type(db_session) -> None:
    ctx = await make_context(db_session)
    for entry_type, quantity in _ALL_TYPES_SCENARIO:
        db_session.add(
            _event(
                ctx,
                entry_type,
                quantity,
                reason="Причина" if entry_type in REASON_REQUIRED_ENTRY_TYPES else None,
                actor_account_id=ctx.actor_id if entry_type in ACTOR_REQUIRED_ENTRY_TYPES else None,
            )
        )
    await db_session.flush()

    assert (
        await get_balance(db_session, ctx.employee_id, ctx.leave_type_id, YEAR_START)
        == _ALL_TYPES_EXPECTED
    )


async def test_balance_ignores_other_employees_types_and_working_years(db_session) -> None:
    ctx = await make_context(db_session)
    other = await make_context(db_session, suffix="b")
    next_year = datetime.date(2027, 1, 1)
    db_session.add_all(
        [
            _event(ctx, "accrual", "28.00"),
            _event(ctx, "accrual", "10.00", leave_type_id=ctx.other_leave_type_id),
            _event(ctx, "accrual", "7.00", working_year_start=next_year),
            _event(other, "accrual", "99.00"),
        ]
    )
    await db_session.flush()

    assert await get_balance(db_session, ctx.employee_id, ctx.leave_type_id, YEAR_START) == Decimal(
        "28.00"
    )


async def test_balance_in_hours_keeps_fractions(db_session) -> None:
    ctx = await make_context(db_session)
    db_session.add_all([_event(ctx, "accrual", "100.25"), _event(ctx, "usage", "-0.75")])
    await db_session.flush()

    assert await get_balance(db_session, ctx.employee_id, ctx.leave_type_id, YEAR_START) == Decimal(
        "99.50"
    )


# --- ручная корректировка: FR-BAL-004 -------------------------------------------


@pytest.mark.parametrize("reason", ["", " ", "   ", "\t", "\n", " \t\n ", " ", " "])
async def test_fr_bal_004_adjustment_without_a_reason_is_refused_and_writes_nothing(
    db_session, reason: str
) -> None:
    """`FR-BAL-004`: WHEN HR вносит ручную корректировку, THE SYSTEM SHALL
    требовать указания причины (первая половина критерия; вторая - запись в
    журнале аудита - за `T-M6-02`)."""
    ctx = await make_context(db_session)

    with pytest.raises(BalanceEntryError):
        await _adjust(db_session, ctx, "2", reason, ctx.actor_id)

    assert await _count(db_session) == 0


async def test_fr_bal_004_adjustment_with_a_reason_is_accepted(db_session) -> None:
    ctx = await make_context(db_session)

    balance = await _adjust(db_session, ctx, "3", "Перенос из прошлой системы", ctx.actor_id)

    assert balance == Decimal("3.00")
    assert await _count(db_session) == 1


async def test_reason_is_stored_without_surrounding_whitespace(db_session) -> None:
    ctx = await make_context(db_session)

    await _adjust(db_session, ctx, "3", "  Перенос данных \n", ctx.actor_id)

    (entry,) = await list_entries(db_session, ctx.employee_id)
    assert entry.reason == "Перенос данных"


async def test_adjustment_requires_an_author(db_session) -> None:
    ctx = await make_context(db_session)

    with pytest.raises(BalanceEntryError):
        await _adjust(db_session, ctx, "3", "Причина", None)  # type: ignore[arg-type]

    assert await _count(db_session) == 0


async def test_adjustment_by_zero_is_refused(db_session) -> None:
    ctx = await make_context(db_session)

    with pytest.raises(BalanceEntryError):
        await _adjust(db_session, ctx, "0", "Причина", ctx.actor_id)

    assert await _count(db_session) == 0


async def test_adjustment_with_an_inverted_working_year_is_refused(db_session) -> None:
    ctx = await make_context(db_session)

    with pytest.raises(BalanceEntryError):
        await adjust_balance(
            db_session,
            employee_id=ctx.employee_id,
            leave_type_id=ctx.leave_type_id,
            working_year_start=YEAR_END,
            working_year_end=YEAR_START,
            quantity=Decimal("1"),
            reason="Причина",
            actor_account_id=ctx.actor_id,
            entry_date=EVENT_DATE,
        )

    assert await _count(db_session) == 0


async def test_an_adjustment_writes_exactly_one_event_and_leaves_earlier_ones_alone(
    db_session,
) -> None:
    ctx = await make_context(db_session)
    db_session.add(_event(ctx, "accrual", "28.00"))
    await db_session.flush()
    before = [
        (e.id, e.entry_type, e.quantity, e.reason, e.actor_account_id)
        for e in await list_entries(db_session, ctx.employee_id)
    ]

    await _adjust(db_session, ctx, "2", "Причина", ctx.actor_id)

    after = await list_entries(db_session, ctx.employee_id)
    assert len(after) == len(before) + 1
    assert [
        (e.id, e.entry_type, e.quantity, e.reason, e.actor_account_id) for e in after[:1]
    ] == before
    assert after[-1].entry_type == "manual_adjustment"


# --- главный тест журнала: две корректировки не затирают друг друга --------------


async def test_two_adjustments_keep_both_reasons_and_both_authors(db_session) -> None:
    """Ради этого остаток - журнал: при одной колонке `adjustment_reason`
    вторая причина затёрла бы первую (`FR-BAL-004`)."""
    ctx = await make_context(db_session)

    await _adjust(db_session, ctx, "5", "Перенос остатка из прошлой системы", ctx.actor_id)
    await _adjust(db_session, ctx, "-2", "Ошибка в переносе: лишние два дня", ctx.other_actor_id)

    entries = await list_entries(db_session, ctx.employee_id)
    assert [(e.reason, e.actor_account_id, e.quantity) for e in entries] == [
        ("Перенос остатка из прошлой системы", ctx.actor_id, Decimal("5.00")),
        ("Ошибка в переносе: лишние два дня", ctx.other_actor_id, Decimal("-2.00")),
    ]
    assert await get_balance(db_session, ctx.employee_id, ctx.leave_type_id, YEAR_START) == Decimal(
        "3.00"
    )


# --- история: FR-BAL-002 ---------------------------------------------------------


async def test_history_is_ordered_by_event_date_then_by_write_order(db_session) -> None:
    ctx = await make_context(db_session)
    march, april, may = (datetime.date(2026, m, 1) for m in (3, 4, 5))
    db_session.add_all(
        [
            _event(ctx, "usage", "-5", entry_date=may),
            _event(ctx, "accrual", "28", entry_date=march),
        ]
    )
    await db_session.flush()
    await _adjust(db_session, ctx, "1", "Первая в апреле", ctx.actor_id, entry_date=april)
    await _adjust(db_session, ctx, "1", "Вторая в апреле", ctx.actor_id, entry_date=april)

    entries = await list_entries(db_session, ctx.employee_id)

    assert [(e.entry_type, e.entry_date.month) for e in entries] == [
        ("accrual", 3),
        ("manual_adjustment", 4),
        ("manual_adjustment", 4),
        ("usage", 5),
    ]
    assert [e.reason for e in entries if e.entry_date.month == 4] == [
        "Первая в апреле",
        "Вторая в апреле",
    ]


async def test_history_can_be_limited_to_one_leave_type_and_never_shows_other_employees(
    db_session,
) -> None:
    ctx = await make_context(db_session)
    other = await make_context(db_session, suffix="b")
    db_session.add_all(
        [
            _event(ctx, "accrual", "28"),
            _event(ctx, "accrual", "10", leave_type_id=ctx.other_leave_type_id),
            _event(other, "accrual", "99"),
        ]
    )
    await db_session.flush()

    everything = await list_entries(db_session, ctx.employee_id)
    one_type = await list_entries(db_session, ctx.employee_id, ctx.leave_type_id)

    assert sorted(e.quantity for e in everything) == [Decimal("10.00"), Decimal("28.00")]
    assert [e.quantity for e in one_type] == [Decimal("28.00")]
