"""`/api/leave-types*` (T-M2-10, FR-ADMIN-001, раздел 6 требований v1.26)."""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import get_args

from sqlalchemy import select, text

from app.models.leave_balance_entry import MANUAL_ADJUSTMENT, LeaveBalanceEntry
from app.models.leave_type import (
    LEAVE_TYPE_CARRY_OVER_VALUES,
    LEAVE_TYPE_COUNTED_NON_WORKING_DAYS_VALUES,
    LEAVE_TYPE_PAID_VALUES,
    LEAVE_TYPE_UNIT_VALUES,
    LeaveType,
)
from app.schemas.leave_type import CarryOverPolicy, CountedNonWorkingDays, IsPaid, Unit
from tests.modules.auth.factories import bearer_header, make_account, make_employee

_URL = "/api/leave-types"

# Тексты отказов дословно, с префиксом поля (`T-M2-27`): префикс и текст, а не подстрока.
_CODE_IMMUTABLE = (
    "code: Код вида отпуска после создания не меняется: по нему находится основной вид, "
    "переименование сломало бы начисление. Меняется наименование"
)
_MAIN_DEACTIVATED = (
    "is_active: Основной оплачиваемый отпуск нельзя деактивировать: "
    "от него зависит начисление (FR-BAL-001)"
)
_MAIN_UNTRACKED = (
    "balance_tracked: Основной оплачиваемый отпуск нельзя лишить признака "
    "«по виду ведётся остаток»: от него зависит начисление (FR-BAL-001)"
)


def _body(**overrides) -> dict:
    body = {
        "code": "comp_leave",
        "name": "Компенсационный отпуск",
        "is_paid": "full",
        "unit": "calendar_days",
        "reduces_main_balance": False,
        "balance_tracked": True,
        "requires_document": False,
        "requires_approval": True,
        "annual_limit": "5.00",
        "carry_over_policy": "none",
        "counted_non_working_days": "all",
        "is_active": True,
    }
    body.update(overrides)
    return body


async def _account(db_session, role: str):
    holder = await make_employee(db_session, email=f"lt-{role}{id(object())}@company.local")
    return await make_account(db_session, employee_id=holder.id, auth_type="local", role=role)


async def _type_id(db_session, code: str) -> int:
    return (
        await db_session.execute(text("SELECT id FROM leave_types WHERE code = :c"), {"c": code})
    ).scalar_one()


# --- GET ---------------------------------------------------------------------------------


async def test_any_authenticated_account_reads_all_attributes_of_the_directory(
    db_session, api_client
) -> None:
    account = await _account(db_session, "employee")

    response = await api_client.get(_URL, headers=bearer_header(account))

    assert response.status_code == 200
    by_code = {item["code"]: item for item in response.json()}
    assert {"annual_paid", "time_off", "business_trip"} <= set(by_code)
    assert set(by_code["annual_paid"]) == {
        "id",
        "code",
        "name",
        "is_paid",
        "unit",
        "reduces_main_balance",
        "balance_tracked",
        "requires_document",
        "requires_approval",
        "annual_limit",
        "carry_over_policy",
        "counted_non_working_days",
        "is_active",
    }
    assert by_code["time_off"]["balance_tracked"] is True
    assert by_code["business_trip"]["balance_tracked"] is False


# --- POST --------------------------------------------------------------------------------


async def test_hr_creates_a_type_with_every_attribute(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin")

    response = await api_client.post(_URL, json=_body(), headers=bearer_header(hr))

    assert response.status_code == 201
    created = response.json()
    assert created["code"] == "comp_leave" and created["balance_tracked"] is True
    row = (
        await db_session.execute(select(LeaveType).where(LeaveType.code == "comp_leave"))
    ).scalar_one()
    assert (row.annual_limit, row.balance_tracked, row.carry_over_policy) == (
        Decimal("5.00"),
        True,
        "none",
    )


async def test_system_admin_may_also_write_the_directory(db_session, api_client) -> None:
    admin = await _account(db_session, "system_admin")  # матрица раздела 7: HR и Админ

    response = await api_client.post(
        _URL, json=_body(code="by_admin"), headers=bearer_header(admin)
    )

    assert response.status_code == 201


async def test_an_employee_may_not_write_the_directory(db_session, api_client) -> None:
    employee = await _account(db_session, "employee")
    existing = await _type_id(db_session, "annual_paid")

    post = await api_client.post(_URL, json=_body(), headers=bearer_header(employee))
    put = await api_client.put(
        f"{_URL}/{existing}", json=_body(code="annual_paid"), headers=bearer_header(employee)
    )

    assert (post.status_code, put.status_code) == (403, 403)


async def test_an_accountant_may_not_write_the_directory(db_session, api_client) -> None:
    accountant = await _account(db_session, "accountant")
    existing = await _type_id(db_session, "annual_paid")

    post = await api_client.post(_URL, json=_body(), headers=bearer_header(accountant))
    put = await api_client.put(
        f"{_URL}/{existing}", json=_body(code="annual_paid"), headers=bearer_header(accountant)
    )

    assert (post.status_code, put.status_code) == (403, 403)
    created = await db_session.scalar(select(LeaveType).where(LeaveType.code == "comp_leave"))
    assert created is None


async def test_a_duplicate_code_is_a_conflict(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin")

    response = await api_client.post(
        _URL, json=_body(code="annual_paid"), headers=bearer_header(hr)
    )

    assert response.status_code == 409


async def test_a_string_attribute_outside_its_set_is_422(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin")
    for field, value in (
        ("is_paid", "sometimes"),
        ("unit", "minutes"),
        ("carry_over_policy", "forever"),
        ("counted_non_working_days", "holidays"),
    ):
        response = await api_client.post(
            _URL, json=_body(**{field: value}), headers=bearer_header(hr)
        )

        assert response.status_code == 422, field


async def test_the_flag_and_the_other_attributes_are_required_on_create(
    db_session, api_client
) -> None:
    hr = await _account(db_session, "hr_admin")
    body = _body()
    del body["balance_tracked"]

    response = await api_client.post(_URL, json=body, headers=bearer_header(hr))

    assert response.status_code == 422  # умолчание не выдумывается: HR задаёт признак явно


async def test_a_malformed_code_and_a_negative_limit_are_422(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin")

    bad_code = await api_client.post(_URL, json=_body(code="Bad Code"), headers=bearer_header(hr))
    bad_limit = await api_client.post(
        _URL, json=_body(annual_limit="-1"), headers=bearer_header(hr)
    )

    assert (bad_code.status_code, bad_limit.status_code) == (422, 422)


def test_schema_value_sets_are_the_same_sets_the_model_constraints_use() -> None:
    """Один набор, одно место: схема не перепечатывает литералы."""
    assert get_args(IsPaid) == LEAVE_TYPE_PAID_VALUES
    assert get_args(Unit) == LEAVE_TYPE_UNIT_VALUES
    assert get_args(CarryOverPolicy) == LEAVE_TYPE_CARRY_OVER_VALUES
    assert get_args(CountedNonWorkingDays) == LEAVE_TYPE_COUNTED_NON_WORKING_DAYS_VALUES


# --- PUT ---------------------------------------------------------------------------------


async def test_hr_edits_a_type_and_the_code_is_untouched(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin")
    type_id = await _type_id(db_session, "study")

    ok = await api_client.put(
        f"{_URL}/{type_id}",
        json=_body(code="study", name="Учебный отпуск (правка)", is_paid="partial"),
        headers=bearer_header(hr),
    )
    assert ok.status_code == 200
    assert ok.json()["name"] == "Учебный отпуск (правка)" and ok.json()["code"] == "study"
    assert ok.json()["is_paid"] == "partial"


async def test_the_body_without_a_code_edits_the_type(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin")
    type_id = await _type_id(db_session, "study")
    body = _body()
    del body["code"]

    response = await api_client.put(f"{_URL}/{type_id}", json=body, headers=bearer_header(hr))

    assert response.status_code == 200
    assert response.json()["code"] == "study"


async def test_changing_the_code_is_refused_not_ignored(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin")
    main_id = await _type_id(db_session, "annual_paid")

    response = await api_client.put(
        f"{_URL}/{main_id}", json=_body(code="renamed_main"), headers=bearer_header(hr)
    )

    assert response.status_code == 400
    assert response.json()["detail"] == _CODE_IMMUTABLE
    assert await _type_id(db_session, "annual_paid") == main_id  # по коду основной вид находится


async def test_the_flag_cannot_be_cleared_on_a_type_with_journal_events(
    db_session, api_client
) -> None:
    hr = await _account(db_session, "hr_admin")
    employee = await make_employee(db_session, email="lt-events@company.local")
    type_id = await _type_id(db_session, "time_off")
    db_session.add(
        LeaveBalanceEntry(
            employee_id=employee.id,
            leave_type_id=type_id,
            entry_type=MANUAL_ADJUSTMENT,
            quantity=Decimal("8"),
            entry_date=datetime.date(2026, 1, 1),
            working_year_start=datetime.date(2026, 1, 1),
            working_year_end=datetime.date(2027, 1, 1),
            reason="Переработка",
            actor_account_id=hr.id,
        )
    )
    await db_session.flush()

    response = await api_client.put(
        f"{_URL}/{type_id}",
        json=_body(code="time_off", balance_tracked=False),
        headers=bearer_header(hr),
    )

    assert response.status_code == 409
    assert "события" in response.json()["detail"]
    flag = await db_session.scalar(select(LeaveType.balance_tracked).where(LeaveType.id == type_id))
    assert flag is True


async def test_the_flag_can_be_cleared_without_events_and_set_again(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin")
    type_id = await _type_id(db_session, "additional_paid")

    cleared = await api_client.put(
        f"{_URL}/{type_id}",
        json=_body(code="additional_paid", balance_tracked=False),
        headers=bearer_header(hr),
    )
    restored = await api_client.put(
        f"{_URL}/{type_id}",
        json=_body(code="additional_paid", balance_tracked=True),
        headers=bearer_header(hr),
    )

    assert cleared.json()["balance_tracked"] is False
    assert restored.json()["balance_tracked"] is True


async def test_editing_an_unknown_type_is_404(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin")

    response = await api_client.put(f"{_URL}/999999", json=_body(), headers=bearer_header(hr))

    assert response.status_code == 404


async def test_there_is_no_delete(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin")
    type_id = await _type_id(db_session, "study")

    response = await api_client.delete(f"{_URL}/{type_id}", headers=bearer_header(hr))

    assert response.status_code == 405
    assert await _type_id(db_session, "study") == type_id


# --- основной вид защищён от правки справочника (PR #182 check, п. 1) ---------------------


async def test_the_main_type_cannot_lose_the_flag_even_on_a_base_without_events(
    db_session, api_client
) -> None:
    hr = await _account(db_session, "hr_admin")
    main_id = await _type_id(db_session, "annual_paid")
    assert (await db_session.scalar(select(LeaveBalanceEntry.id).limit(1))) is None  # событий нет

    response = await api_client.put(
        f"{_URL}/{main_id}",
        json=_body(code="annual_paid", balance_tracked=False),
        headers=bearer_header(hr),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == _MAIN_UNTRACKED
    flag = await db_session.scalar(select(LeaveType.balance_tracked).where(LeaveType.id == main_id))
    assert flag is True


async def test_the_main_type_cannot_be_deactivated(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin")
    main_id = await _type_id(db_session, "annual_paid")

    response = await api_client.put(
        f"{_URL}/{main_id}",
        json=_body(code="annual_paid", is_active=False),
        headers=bearer_header(hr),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == _MAIN_DEACTIVATED
    active = await db_session.scalar(select(LeaveType.is_active).where(LeaveType.id == main_id))
    assert active is True


async def test_the_main_type_with_both_fields_violated_names_both_in_a_fixed_order(
    db_session, api_client
) -> None:
    # Оба поля нарушены разом: отказ называет ОБА, в порядке is_active, balance_tracked, а не то,
    # которое проверка увидела бы первым.
    hr = await _account(db_session, "hr_admin")
    main_id = await _type_id(db_session, "annual_paid")

    response = await api_client.put(
        f"{_URL}/{main_id}",
        json=_body(code="annual_paid", is_active=False, balance_tracked=False),
        headers=bearer_header(hr),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == f"{_MAIN_DEACTIVATED}; {_MAIN_UNTRACKED}"


async def test_a_code_change_wins_over_the_main_type_protection(db_session, api_client) -> None:
    # Два РАЗНЫХ отказа (смена кода и защита основного вида) отвечают по одному: сначала код.
    hr = await _account(db_session, "hr_admin")
    main_id = await _type_id(db_session, "annual_paid")

    response = await api_client.put(
        f"{_URL}/{main_id}",
        json=_body(code="renamed_main", is_active=False),
        headers=bearer_header(hr),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == _CODE_IMMUTABLE


async def test_the_main_type_is_still_editable_otherwise(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin")
    main_id = await _type_id(db_session, "annual_paid")

    response = await api_client.put(
        f"{_URL}/{main_id}",
        json=_body(code="annual_paid", name="Основной отпуск (правка)"),
        headers=bearer_header(hr),
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Основной отпуск (правка)"


async def test_another_type_may_be_deactivated_and_lose_the_flag(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin")
    type_id = await _type_id(db_session, "time_off")

    response = await api_client.put(
        f"{_URL}/{type_id}",
        json=_body(code="time_off", is_active=False, balance_tracked=False),
        headers=bearer_header(hr),
    )

    assert response.status_code == 200
    assert (response.json()["is_active"], response.json()["balance_tracked"]) == (False, False)
