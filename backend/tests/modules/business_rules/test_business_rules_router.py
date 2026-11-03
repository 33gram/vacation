"""`GET/PUT /api/admin/business-rules` (T-M2-12, FR-ADMIN-004; матрица раздела 7 v1.27).

`FR-ADMIN-004` - один критерий, исходов несколько: чтение, запись, отказы, доступ.
"""

from __future__ import annotations

import datetime
import itertools

import pytest
from sqlalchemy import text

from app.modules.business_rules import service
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


async def test_the_service_writes_its_own_author_and_time_not_the_database_clock(
    db_session, api_client, monkeypatch
) -> None:
    # Фикстура держит ОДНУ транзакцию, `now()` БД в ней константа, поэтому «время сдвинулось»
    # механизмы не различает. Часы приложения подменены: в строке должно оказаться именно то
    # значение, которое поставил сервис, - при двух разных правках двумя разными аккаунтами.
    hr = await _account(db_session, "hr_admin")
    admin = await _account(db_session, "system_admin")
    first_time = datetime.datetime(2031, 1, 2, 3, 4, 5, tzinfo=datetime.UTC)
    second_time = datetime.datetime(2032, 6, 7, 8, 9, 10, tzinfo=datetime.UTC)

    monkeypatch.setattr(service, "utcnow", lambda: first_time)
    await api_client.put(
        _URL, json={"values": {"annual_leave_days": "30"}}, headers=bearer_header(hr)
    )
    first = await _row(db_session, "annual_leave_days")
    monkeypatch.setattr(service, "utcnow", lambda: second_time)
    await api_client.put(
        _URL, json={"values": {"annual_leave_days": "31"}}, headers=bearer_header(admin)
    )
    second = await _row(db_session, "annual_leave_days")

    assert (first.value, first.updated_by_account_id, first.updated_at) == (
        "30",
        hr.id,
        first_time,
    )
    assert (second.value, second.updated_by_account_id, second.updated_at) == (
        "31",
        admin.id,
        second_time,
    )


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
    # Строка ЦЕЛИКОМ, а не вхождения: её читает `rulesErrorsByKey`
    # (`frontend/src/entities/businessRules.ts`) и раскладывает по полям, разделяя на `"; "`
    # перед очередным ключом. Смена разделителя (скажем, на `", "`) молча вернула бы на экране
    # один общий баннер вместо подписей под двумя полями, а проверка вхождений промолчала бы.
    assert response.json()["detail"] == (
        "annual_leave_days: значение недопустимо, нужно целое число от 1 до 366; "
        "approval_escalation_working_days: значение недопустимо, нужно целое число от 1 до 60"
    )
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
    before = await _row(db_session, "annual_leave_days")
    new_value = str(int(before.value) + 1)  # ОТЛИЧНОЕ от текущего: запись должна что-то менять

    read = await api_client.get(_URL, headers=bearer_header(account))
    write = await api_client.put(
        _URL, json={"values": {"annual_leave_days": new_value}}, headers=bearer_header(account)
    )

    assert (read.status_code, write.status_code) == (200, 200)
    after = await _row(db_session, "annual_leave_days")
    assert (after.value, after.updated_by_account_id) == (new_value, account.id)
    assert after.updated_at > before.updated_at


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


# --- пробелы, найденные ревью (PR #183 review, п. 6, 7, 4) -------------------------------


async def test_without_a_token_both_methods_are_401(api_client) -> None:
    assert (await api_client.get(_URL)).status_code == 401
    put = await api_client.put(_URL, json={"values": {"annual_leave_days": "30"}})
    assert put.status_code == 401


async def test_an_account_without_an_employee_card_passes_by_role(db_session, api_client) -> None:
    # Служебная учётная запись: login вместо карточки (`ck_accounts_employee_or_login`).
    service_account = await make_account(
        db_session, login="svc-rules", auth_type="local", role="system_admin"
    )

    read = await api_client.get(_URL, headers=bearer_header(service_account))
    write = await api_client.put(
        _URL, json={"values": {"annual_leave_days": "30"}}, headers=bearer_header(service_account)
    )

    assert (read.status_code, write.status_code) == (200, 200)
    row = {i["key"]: i for i in write.json()}["annual_leave_days"]
    assert row["updated_by_name"] == "Администратор"  # нейтральная подпись без карточки


@pytest.mark.parametrize("role", ["hr_admin", "system_admin"])
async def test_a_pending_password_change_blocks_the_rules_for_both_roles(
    db_session, api_client, role
) -> None:
    holder = await make_employee(db_session, email=f"br-gate-{role}@company.local")
    account = await make_account(
        db_session,
        employee_id=holder.id,
        auth_type="local",
        role=role,
        must_change_password=True,
    )
    before = await _row(db_session, "annual_leave_days")

    read = await api_client.get(_URL, headers=bearer_header(account))
    write = await api_client.put(
        _URL, json={"values": {"annual_leave_days": "30"}}, headers=bearer_header(account)
    )

    assert (read.status_code, write.status_code) == (403, 403)
    assert tuple(await _row(db_session, "annual_leave_days")) == tuple(before)


@pytest.mark.parametrize(
    "value",
    [" 5", "5 ", "+5", "-5", "٥", "１０", "1e2", "5.0", "", "0x10"],
)
async def test_value_strings_are_strict_nothing_is_normalised_into_validity(
    db_session, api_client, value
) -> None:
    hr = await _account(db_session, "hr_admin")
    before = await _row(db_session, "annual_leave_days")

    response = await api_client.put(
        _URL, json={"values": {"annual_leave_days": value}}, headers=bearer_header(hr)
    )

    assert response.status_code == 400
    assert tuple(await _row(db_session, "annual_leave_days")) == tuple(before)


async def test_a_value_type_that_differs_from_the_registry_is_refused(
    db_session, api_client
) -> None:
    # Сверку типа строки с реестром делает `parse_stored_value`: строка помечена как
    # `month_day`, реестр ждёт `integer` - запись отказывает, а не пишет молча.
    hr = await _account(db_session, "hr_admin")
    await db_session.execute(
        text(
            "UPDATE business_rules_config SET value_type = 'month_day', value = '12-17' "
            "WHERE key = 'annual_leave_days'"
        )
    )

    response = await api_client.put(
        _URL, json={"values": {"annual_leave_days": "28"}}, headers=bearer_header(hr)
    )

    assert response.status_code == 400
    assert response.json()["detail"].startswith("annual_leave_days:")
    assert (await _row(db_session, "annual_leave_days")).value == "12-17"


async def test_the_same_number_in_another_spelling_is_not_a_change(
    db_session, api_client
) -> None:
    hr = await _account(db_session, "hr_admin")
    before = await _row(db_session, "annual_leave_days")  # "28", автор None

    response = await api_client.put(
        _URL, json={"values": {"annual_leave_days": "028"}}, headers=bearer_header(hr)
    )

    assert response.status_code == 200
    assert tuple(await _row(db_session, "annual_leave_days")) == tuple(before)  # "28" цел


async def test_a_new_value_is_stored_in_the_canonical_form(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin")

    await api_client.put(
        _URL, json={"values": {"annual_leave_days": "029"}}, headers=bearer_header(hr)
    )

    assert (await _row(db_session, "annual_leave_days")).value == "29"


async def test_a_registry_key_without_a_row_is_hidden_by_get_and_404_for_put(
    db_session, api_client
) -> None:
    # Сегодняшнее поведение для строки, пропавшей после старта (ручное удаление, откат
    # миграции, расхождение БД с реестром): `GET` её не показывает, `PUT` отвечает 404.
    hr = await _account(db_session, "hr_admin")
    await db_session.execute(
        text("DELETE FROM business_rules_config WHERE key = 'min_leave_part_days'")
    )

    read = await api_client.get(_URL, headers=bearer_header(hr))
    write = await api_client.put(
        _URL, json={"values": {"min_leave_part_days": "5"}}, headers=bearer_header(hr)
    )

    assert "min_leave_part_days" not in {i["key"] for i in read.json()}
    assert len(read.json()) == len(RULES) - 1
    assert write.status_code == 404
    assert "min_leave_part_days" in write.json()["detail"]
