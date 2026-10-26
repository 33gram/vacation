"""`GET/PUT /api/admin/business-rules` (T-M2-12, FR-ADMIN-004; матрица раздела 7 v1.27).

`FR-ADMIN-004` - один критерий, исходов несколько: чтение, запись, отказы, доступ.
"""

from __future__ import annotations

import itertools

import pytest
from sqlalchemy import text

from app.modules.business_rules.service import RULES, validate_all
from tests.modules.auth.factories import bearer_header, make_account, make_employee

_URL = "/api/admin/business-rules"
_seq = itertools.count()


async def _account(db_session, role: str, full_name: str = "Тестовый Сотрудник"):
    holder = await make_employee(
        db_session, email=f"br-{role}-{next(_seq)}@company.local", full_name=full_name
    )
    return await make_account(db_session, employee_id=holder.id, auth_type="local", role=role)


async def _row(db_session, key: str):
    return (
        await db_session.execute(
            text(
                "SELECT value, updated_by_account_id, updated_at FROM business_rules_config "
                "WHERE key = :k"
            ),
            {"k": key},
        )
    ).one()


# --- чтение ------------------------------------------------------------------------------


async def test_get_returns_the_whole_registry_with_ranges(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin")

    response = await api_client.get(_URL, headers=bearer_header(hr))

    assert response.status_code == 200
    by_key = {item["key"]: item for item in response.json()}
    assert list(by_key) == list(RULES)  # весь реестр, в порядке реестра
    leave_days = by_key["annual_leave_days"]
    assert (leave_days["value"], leave_days["value_type"]) == ("28", "integer")
    assert (leave_days["minimum"], leave_days["maximum"]) == (1, 366)
    assert leave_days["description"]
    assert leave_days["updated_by_name"] is None  # засеяно миграцией, не менялось
    # у месяца-дня диапазона в реестре нет - отдаётся пусто, а не выдумывается
    deadline = by_key["schedule_approval_deadline"]
    assert (deadline["value"], deadline["minimum"], deadline["maximum"]) == ("12-17", None, None)
    for key, spec in RULES.items():
        assert (by_key[key]["minimum"], by_key[key]["maximum"]) == (spec.minimum, spec.maximum)


# --- запись ------------------------------------------------------------------------------


async def test_put_changes_the_value_the_author_and_the_time(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin", full_name="Иванова Анна")
    before = await _row(db_session, "annual_leave_days")

    response = await api_client.put(
        _URL, json={"values": {"annual_leave_days": "30"}}, headers=bearer_header(hr)
    )

    assert response.status_code == 200
    item = {i["key"]: i for i in response.json()}["annual_leave_days"]
    assert item["value"] == "30"
    assert item["updated_by_name"] == "Иванова Анна"
    after = await _row(db_session, "annual_leave_days")
    assert after.value == "30"
    assert after.updated_by_account_id == hr.id
    assert after.updated_at > before.updated_at  # время проставил сервис


async def test_a_second_writer_replaces_the_author_and_moves_the_time(
    db_session, api_client
) -> None:
    # Автор и время ставятся явно сервисом, а не `onupdate` модели: вторая правка другим
    # аккаунтом обновляет обе колонки.
    hr = await _account(db_session, "hr_admin")
    admin = await _account(db_session, "system_admin")
    await api_client.put(
        _URL, json={"values": {"annual_leave_days": "30"}}, headers=bearer_header(hr)
    )
    first = await _row(db_session, "annual_leave_days")

    await api_client.put(
        _URL, json={"values": {"annual_leave_days": "31"}}, headers=bearer_header(admin)
    )

    second = await _row(db_session, "annual_leave_days")
    assert (second.value, second.updated_by_account_id) == ("31", admin.id)
    assert second.updated_at > first.updated_at


async def test_a_pair_with_the_same_value_does_not_touch_author_or_time(
    db_session, api_client
) -> None:
    hr = await _account(db_session, "hr_admin")
    before = await _row(db_session, "annual_leave_days")

    response = await api_client.put(
        _URL, json={"values": {"annual_leave_days": before.value}}, headers=bearer_header(hr)
    )

    assert response.status_code == 200
    assert tuple(await _row(db_session, "annual_leave_days")) == tuple(before)


async def test_put_applies_a_set_of_pairs_of_both_types(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin")

    response = await api_client.put(
        _URL,
        json={"values": {"annual_leave_days": "29", "schedule_approval_deadline": "11-30"}},
        headers=bearer_header(hr),
    )

    assert response.status_code == 200
    assert (await _row(db_session, "annual_leave_days")).value == "29"
    assert (await _row(db_session, "schedule_approval_deadline")).value == "11-30"


# --- отказы ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("annual_leave_days", "0"),  # ниже диапазона
        ("annual_leave_days", "367"),  # выше диапазона
        ("password_min_length", "7"),  # граница FR-AUTH-002/15
        ("annual_leave_days", "двадцать"),  # не число
        ("schedule_approval_deadline", "02-30"),  # несуществующая дата
        ("schedule_approval_deadline", "02-29"),  # ежегодный срок, которого нет в большинстве лет
    ],
)
async def test_an_invalid_value_is_400_with_the_key_and_nothing_is_written(
    db_session, api_client, key, value
) -> None:
    hr = await _account(db_session, "hr_admin")
    before = await _row(db_session, key)

    response = await api_client.put(
        _URL, json={"values": {key: value}}, headers=bearer_header(hr)
    )

    assert response.status_code == 400
    assert response.json()["detail"].startswith(f"{key}:")
    assert tuple(await _row(db_session, key)) == tuple(before)


async def test_two_invalid_pairs_are_both_named_and_the_valid_one_is_not_written(
    db_session, api_client
) -> None:
    hr = await _account(db_session, "hr_admin")
    before = await _row(db_session, "min_leave_part_days")

    response = await api_client.put(
        _URL,
        json={
            "values": {
                "annual_leave_days": "0",
                "min_leave_part_days": "5",  # годное значение между двумя негодными
                "approval_escalation_working_days": "999",
            }
        },
        headers=bearer_header(hr),
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "annual_leave_days:" in detail
    assert "approval_escalation_working_days:" in detail
    assert "min_leave_part_days" not in detail
    assert tuple(await _row(db_session, "min_leave_part_days")) == tuple(before)


async def test_an_unknown_key_is_404_and_nothing_is_written(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin")
    before = await _row(db_session, "annual_leave_days")

    response = await api_client.put(
        _URL,
        json={"values": {"annual_leave_days": "30", "no_such_rule": "1"}},
        headers=bearer_header(hr),
    )

    assert response.status_code == 404
    assert "no_such_rule" in response.json()["detail"]
    assert tuple(await _row(db_session, "annual_leave_days")) == tuple(before)
    count = (
        await db_session.execute(
            text("SELECT count(*) FROM business_rules_config WHERE key = 'no_such_rule'")
        )
    ).scalar_one()
    assert count == 0  # ключ эндпоинтом не создаётся


async def test_an_unknown_key_wins_over_an_invalid_value(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin")

    response = await api_client.put(
        _URL,
        json={"values": {"annual_leave_days": "0", "no_such_rule": "1"}},
        headers=bearer_header(hr),
    )

    assert response.status_code == 404


@pytest.mark.parametrize("body", [{"values": {"annual_leave_days": 30}}, {"values": {}}, {}])
async def test_a_malformed_body_is_422(db_session, api_client, body) -> None:
    hr = await _account(db_session, "hr_admin")

    response = await api_client.put(_URL, json=body, headers=bearer_header(hr))

    assert response.status_code == 422


# --- доступ ------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["hr_admin", "system_admin"])
async def test_hr_and_system_admin_can_read_and_write(db_session, api_client, role) -> None:
    account = await _account(db_session, role)

    read = await api_client.get(_URL, headers=bearer_header(account))
    write = await api_client.put(
        _URL, json={"values": {"annual_leave_days": "28"}}, headers=bearer_header(account)
    )

    assert (read.status_code, write.status_code) == (200, 200)


@pytest.mark.parametrize("role", ["employee", "accountant"])
async def test_other_roles_can_neither_read_nor_write(db_session, api_client, role) -> None:
    account = await _account(db_session, role)
    before = await _row(db_session, "annual_leave_days")

    read = await api_client.get(_URL, headers=bearer_header(account))
    write = await api_client.put(
        _URL, json={"values": {"annual_leave_days": "30"}}, headers=bearer_header(account)
    )

    assert (read.status_code, write.status_code) == (403, 403)
    assert tuple(await _row(db_session, "annual_leave_days")) == tuple(before)


# --- согласие записи и стартовой проверки ------------------------------------------------


async def test_after_a_write_the_startup_validation_still_passes(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin")
    # границы диапазона: запись принимает ровно то, что принимает чтение
    values = {
        spec.key: str(spec.minimum if index % 2 else spec.maximum)
        for index, spec in enumerate(s for s in RULES.values() if s.minimum is not None)
    }
    values["schedule_approval_deadline"] = "01-31"

    response = await api_client.put(_URL, json={"values": values}, headers=bearer_header(hr))

    assert response.status_code == 200
    await validate_all(db_session)  # не поднимает
