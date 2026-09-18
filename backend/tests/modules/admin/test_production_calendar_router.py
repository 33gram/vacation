"""Интеграционные тесты `/api/admin/production-calendar/{year}` (T-M1-17,
FR-ADMIN-003)."""

from __future__ import annotations

from tests.modules.auth.factories import bearer_header, make_account, make_employee


async def _make_admin(db_session):
    employee = await make_employee(db_session, email=f"admin{id(object())}@company.local")
    return await make_account(
        db_session, employee_id=employee.id, auth_type="local", role="system_admin"
    )


async def _make_employee_account(db_session):
    employee = await make_employee(db_session, email=f"emp{id(object())}@company.local")
    return await make_account(
        db_session, employee_id=employee.id, auth_type="local", role="employee"
    )


async def test_requires_system_admin_role(db_session, api_client) -> None:
    account = await _make_employee_account(db_session)

    response = await api_client.get(
        "/api/admin/production-calendar/2027", headers=bearer_header(account)
    )

    assert response.status_code == 403


async def test_get_empty_year_returns_empty_list(db_session, api_client) -> None:
    account = await _make_admin(db_session)

    response = await api_client.get(
        "/api/admin/production-calendar/2027", headers=bearer_header(account)
    )

    assert response.status_code == 200
    assert response.json() == {"year": 2027, "days": []}


async def test_put_loads_year_and_get_returns_it(db_session, api_client) -> None:
    account = await _make_admin(db_session)
    payload = {
        "days": [
            {"date": "2027-01-01", "day_type": "holiday"},
            {"date": "2027-01-02", "day_type": "holiday"},
            {"date": "2027-01-04", "day_type": "working"},
        ]
    }

    put_response = await api_client.put(
        "/api/admin/production-calendar/2027", json=payload, headers=bearer_header(account)
    )
    get_response = await api_client.get(
        "/api/admin/production-calendar/2027", headers=bearer_header(account)
    )

    assert put_response.status_code == 200
    assert put_response.json() == get_response.json()
    assert len(get_response.json()["days"]) == 3


async def test_put_replaces_previous_year_contents(db_session, api_client) -> None:
    account = await _make_admin(db_session)
    await api_client.put(
        "/api/admin/production-calendar/2027",
        json={"days": [{"date": "2027-01-01", "day_type": "holiday"}]},
        headers=bearer_header(account),
    )

    response = await api_client.put(
        "/api/admin/production-calendar/2027",
        json={"days": [{"date": "2027-06-01", "day_type": "working"}]},
        headers=bearer_header(account),
    )

    assert response.status_code == 200
    days = response.json()["days"]
    assert len(days) == 1
    assert days[0]["date"] == "2027-06-01"


async def test_put_rejects_date_outside_requested_year(db_session, api_client) -> None:
    account = await _make_admin(db_session)

    response = await api_client.put(
        "/api/admin/production-calendar/2027",
        json={"days": [{"date": "2028-01-01", "day_type": "holiday"}]},
        headers=bearer_header(account),
    )

    assert response.status_code == 400


async def test_put_years_are_independent(db_session, api_client) -> None:
    account = await _make_admin(db_session)
    await api_client.put(
        "/api/admin/production-calendar/2027",
        json={"days": [{"date": "2027-01-01", "day_type": "holiday"}]},
        headers=bearer_header(account),
    )

    response = await api_client.get(
        "/api/admin/production-calendar/2028", headers=bearer_header(account)
    )

    assert response.json() == {"year": 2028, "days": []}
