"""`GET /api/auth/me` отдаёт бизнес-день (T-M2-36/T-M2-38, `NFR-LOC-002`, решение 38 раздела 13).

Момент зафиксирован внутри окна расхождения: 2026-11-12T22:00Z - по Москве уже 13.11, по UTC
ещё 12.11. Поле берётся из `business_today` (единственный читатель параметра), поэтому при
параметре `Europe/Moscow` и при `UTC` оно разное - тем же способом, что в
`test_business_day_endpoints.py`."""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import text

from app.modules.business_rules import business_day
from tests.modules.auth.factories import bearer_header, make_account, make_employee

MOMENT = datetime.datetime(2026, 11, 12, 22, 0, tzinfo=datetime.UTC)


@pytest.fixture(autouse=True)
def _frozen_business_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(business_day, "utcnow", lambda: MOMENT)


async def _account(db_session, **kwargs):
    employee = await make_employee(db_session, email="me-day@company.local")
    return await make_account(db_session, employee_id=employee.id, auth_type="local", **kwargs)


async def test_me_returns_the_business_day_of_the_organization_zone(db_session, api_client) -> None:
    account = await _account(db_session)

    response = await api_client.get("/api/auth/me", headers=bearer_header(account))

    assert response.status_code == 200
    assert response.json()["today"] == "2026-11-13"  # Москва: 01:00 13.11


async def test_me_day_follows_the_zone_parameter_for_one_moment(db_session, api_client) -> None:
    account = await _account(db_session)
    await db_session.execute(
        text("UPDATE business_rules_config SET value = 'UTC' WHERE key = 'business_timezone'")
    )

    response = await api_client.get("/api/auth/me", headers=bearer_header(account))

    assert response.json()["today"] == "2026-11-12"  # тот же момент, иной пояс - иной день


async def test_me_returns_the_day_before_the_initial_password_is_changed(
    db_session, api_client
) -> None:
    # `me` доступен и до смены первичного пароля: день нужен интерфейсу и тогда.
    account = await _account(db_session, must_change_password=True)

    response = await api_client.get("/api/auth/me", headers=bearer_header(account))

    assert response.status_code == 200
    assert response.json()["must_change_password"] is True
    assert response.json()["today"] == "2026-11-13"
