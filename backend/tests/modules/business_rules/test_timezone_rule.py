"""Параметр `business_timezone` и третий тип значения `timezone` (T-M2-34, `NFR-LOC-002`, `BR-12`).

Кто что проверяет (Ф-1): ФОРМУ строки - CHECK базы и сервис ОДНИМ шаблоном (`TIMEZONE_FORM`);
СУЩЕСТВОВАНИЕ зоны - только сервис, через `zoneinfo`. Поэтому `Europe/Moskva` база примет
(форма верна), а сервис откажет: сервис строже базы, не мягче. Тесты ниже показывают оба
направления и что шаблон миграции совпадает с шаблоном модели.
"""

from __future__ import annotations

import datetime
import importlib.util
import re
import time
import zoneinfo
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.business_rule_config import TIMEZONE_FORM
from app.modules.business_rules.service import (
    BUSINESS_TIMEZONE,
    NOT_A_ZONE,
    BusinessRuleError,
    BusinessRulesConfigError,
    RuleValuesInvalidError,
    get_timezone,
    parse_stored_value,
    update_rules,
    validate_all,
)

_MIGRATION = (
    Path(__file__).resolve().parents[3] / "alembic" / "versions" / "0011_business_timezone_rule.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0011", _MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_INSERT = text(
    "INSERT INTO business_rules_config (key, value, value_type, description) "
    "VALUES ('zone_probe', :value, 'timezone', 'Проба')"
)

_GOOD = ["Europe/Moscow", "UTC", "Etc/GMT+3", "America/Argentina/Buenos_Aires", "Asia/Ust-Nera"]
# Форма верна, зоны нет: база примет, сервис откажет.
_RIGHT_FORM_NO_SUCH_ZONE = ["Europe/Moskva", "europe/moscow", "Europe", "Mars/Olympus_Mons"]
# Форма неверна: отказ и у базы, и у сервиса.
_WRONG_FORM = [
    "",
    " Europe/Moscow",
    "Europe/Moscow ",
    "Europe/Moscow\n",
    "Europe Moscow",
    "Европа/Москва",
    "Europe/Москва",
    "../etc/passwd",
    "/Europe/Moscow",
    "Europe/Moscow/",
    "Europe//Moscow",
    "1Europe/Moscow",
]


@pytest.mark.parametrize("name", _GOOD)
def test_a_known_zone_is_parsed_to_that_zone(name: str) -> None:
    zone = parse_stored_value(BUSINESS_TIMEZONE, name, "timezone")

    assert isinstance(zone, zoneinfo.ZoneInfo) and zone.key == name


@pytest.mark.parametrize("name", _RIGHT_FORM_NO_SUCH_ZONE + _WRONG_FORM)
def test_a_bad_name_is_refused_by_the_service_naming_the_key(name: str) -> None:
    with pytest.raises(BusinessRuleError) as excinfo:
        parse_stored_value(BUSINESS_TIMEZONE, name, "timezone")

    assert excinfo.value.key == BUSINESS_TIMEZONE


@pytest.mark.parametrize("name", _GOOD + _RIGHT_FORM_NO_SUCH_ZONE)
async def test_the_database_accepts_every_name_of_the_right_form(db_session, name: str) -> None:
    await db_session.execute(_INSERT, {"value": name})  # CHECK: форма, не существование зоны


@pytest.mark.parametrize("name", _WRONG_FORM)
async def test_the_database_refuses_every_name_of_the_wrong_form(db_session, name: str) -> None:
    with pytest.raises(IntegrityError) as excinfo:
        await db_session.execute(_INSERT, {"value": name})

    assert "ck_business_rules_config_timezone_format" in str(excinfo.value)


@pytest.mark.parametrize("name", _GOOD + _RIGHT_FORM_NO_SUCH_ZONE + _WRONG_FORM)
async def test_the_form_of_the_database_and_of_the_service_are_the_same_template(
    db_session, name: str
) -> None:
    form_ok = re.fullmatch(TIMEZONE_FORM, name) is not None
    try:
        await db_session.execute(_INSERT, {"value": name})
        database_ok = True
    except IntegrityError:
        database_ok = False

    assert database_ok == form_ok


def test_the_template_of_the_migration_equals_the_template_of_the_model() -> None:
    assert _migration()._TIMEZONE_FORM == TIMEZONE_FORM


def test_the_service_is_stricter_than_the_database_by_exactly_the_existence_of_the_zone() -> None:
    for name in _RIGHT_FORM_NO_SUCH_ZONE:
        assert re.fullmatch(TIMEZONE_FORM, name)  # форма верна: база примет
        with pytest.raises(BusinessRuleError):
            parse_stored_value(BUSINESS_TIMEZONE, name, "timezone")


async def test_the_seeded_zone_is_moscow(db_session) -> None:
    zone = await get_timezone(db_session, BUSINESS_TIMEZONE)

    assert zone.key == "Europe/Moscow"


async def test_a_missing_key_is_refused_there_is_no_fallback_in_the_code(db_session) -> None:
    await db_session.execute(
        text("DELETE FROM business_rules_config WHERE key = 'business_timezone'")
    )

    with pytest.raises(BusinessRuleError) as excinfo:
        await get_timezone(db_session, BUSINESS_TIMEZONE)

    assert excinfo.value.key == BUSINESS_TIMEZONE and "missing" in excinfo.value.problem


async def test_a_zone_stored_with_a_wrong_type_is_refused(db_session) -> None:
    await db_session.execute(
        text(
            "UPDATE business_rules_config SET value='12-17', value_type='month_day' "
            "WHERE key = 'business_timezone'"
        )
    )

    with pytest.raises(BusinessRuleError):
        await get_timezone(db_session, BUSINESS_TIMEZONE)


async def _set_zone(db_session, value: str) -> None:
    await db_session.execute(
        text("UPDATE business_rules_config SET value = :value WHERE key = 'business_timezone'"),
        {"value": value},
    )


async def test_startup_validation_sees_a_garbage_zone_in_the_database(db_session) -> None:
    await _set_zone(db_session, "Europe/Moskva")  # форма верна, CHECK пропустил

    with pytest.raises(BusinessRulesConfigError) as excinfo:
        await validate_all(db_session)

    assert [problem.key for problem in excinfo.value.problems] == [BUSINESS_TIMEZONE]


async def test_an_admin_edit_with_a_bad_zone_is_refused_with_a_russian_text(db_session) -> None:
    from tests.modules.auth.factories import make_account, make_employee

    holder = await make_employee(db_session, email="tz-admin@company.local")
    account = await make_account(
        db_session, employee_id=holder.id, role="hr_admin", auth_type="local"
    )

    with pytest.raises(RuleValuesInvalidError) as excinfo:
        await update_rules(
            db_session, {BUSINESS_TIMEZONE: "Europe/Moskva"}, actor_account_id=account.id
        )

    assert excinfo.value.problems[BUSINESS_TIMEZONE].startswith("значение недопустимо")
    assert "Europe/Moscow" in excinfo.value.problems[BUSINESS_TIMEZONE]
    assert (await get_timezone(db_session, BUSINESS_TIMEZONE)).key == "Europe/Moscow"  # не записано


async def test_an_admin_edit_with_a_good_zone_is_stored_in_the_canonical_form(db_session) -> None:
    from tests.modules.auth.factories import make_account, make_employee

    holder = await make_employee(db_session, email="tz-admin2@company.local")
    account = await make_account(
        db_session, employee_id=holder.id, role="hr_admin", auth_type="local"
    )

    await update_rules(db_session, {BUSINESS_TIMEZONE: "UTC"}, actor_account_id=account.id)

    assert (await get_timezone(db_session, BUSINESS_TIMEZONE)).key == "UTC"


async def _run_seed(db_session) -> None:
    seed = _migration().seed_business_timezone
    await db_session.run_sync(lambda session: seed(session.connection()))


async def _zone_rows(db_session) -> list[tuple[str, str]]:
    result = await db_session.execute(
        text("SELECT value, value_type FROM business_rules_config WHERE key = 'business_timezone'")
    )
    return [tuple(row) for row in result]


async def test_the_seed_runs_twice_without_duplicates_and_keeps_an_admin_edit(db_session) -> None:
    await _set_zone(db_session, "Asia/Yekaterinburg")

    await _run_seed(db_session)
    await _run_seed(db_session)

    assert await _zone_rows(db_session) == [("Asia/Yekaterinburg", "timezone")]


async def test_the_seed_restores_a_missing_key_with_the_default(db_session) -> None:
    await db_session.execute(
        text("DELETE FROM business_rules_config WHERE key = 'business_timezone'")
    )

    await _run_seed(db_session)

    assert await _zone_rows(db_session) == [("Europe/Moscow", "timezone")]


# --- Псевдоимена зон (PR #196 review, П-1) ---------------------------------------------------
# `localtime` есть в наборе системного каталога образа и берётся из `/etc/localtime`, то есть
# значение и его результат зависели бы от РАЗВЁРТЫВАНИЯ; `Factory` - заглушка базы зон
# (смещение 0, сокращение «-00»). Оба проходят форму и раньше проходили проверку.


def _index_names() -> set[str]:
    import importlib.resources

    index = importlib.resources.files("tzdata").joinpath("zones").read_text(encoding="utf-8")
    return set(index.split())


@pytest.mark.parametrize("name", ["localtime", "Factory"])
def test_a_pseudo_name_is_refused(name: str) -> None:
    with pytest.raises(BusinessRuleError) as excinfo:
        parse_stored_value(BUSINESS_TIMEZONE, name, "timezone")

    assert excinfo.value.key == BUSINESS_TIMEZONE


@pytest.mark.parametrize("name", ["localtime", "Factory"])
def test_a_pseudo_name_is_refused_whatever_the_image_and_the_process_zone_are(
    name: str, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.resources

    # Имитация образа, где системный каталог зон содержит `localtime` (и `Factory`), а процесс
    # живёт в другом поясе: значение обязано отвергаться и здесь, и в образе без них.
    moscow = importlib.resources.files("tzdata.zoneinfo.Europe").joinpath("Moscow").read_bytes()
    for pseudo in ("localtime", "Factory"):
        (tmp_path / pseudo).write_bytes(moscow)
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    zoneinfo.reset_tzpath(to=[str(tmp_path)])
    zoneinfo.ZoneInfo.clear_cache()
    try:
        # Предусловие: в такой среде голый `ZoneInfo` имя ПРИНИМАЕТ - тест не пуст.
        assert zoneinfo.ZoneInfo(name).utcoffset(datetime.datetime(2026, 1, 1)) is not None
        with pytest.raises(BusinessRuleError):
            parse_stored_value(BUSINESS_TIMEZONE, name, "timezone")
    finally:
        zoneinfo.reset_tzpath()
        zoneinfo.ZoneInfo.clear_cache()
        monkeypatch.undo()
        time.tzset()


def _is_placeholder(name: str) -> bool:
    zone = zoneinfo.ZoneInfo(name)
    samples = [datetime.datetime(year, month, 1) for year in (1990, 2010, 2026) for month in (1, 7)]
    return all(zone.tzname(moment) == "-00" for moment in samples)


def test_the_list_of_names_that_are_not_zones_is_exactly_what_the_tz_database_marks_so() -> None:
    # Сторож не слепнет: перебор ВСЕХ имён индекса пакета `tzdata`. Правило отсечения -
    # сокращение «-00» на всех пробных датах (так база зон помечает «зоны нет»); оно должно
    # дать ровно `NOT_A_ZONE`. Новая заглушка в обновлении `tzdata` покраснит тест, а не пройдёт
    # молча; зона, не попавшая под правило, но лишняя в списке, тоже.
    found = {name for name in _index_names() if _is_placeholder(name)}

    assert found == set(NOT_A_ZONE)


def test_every_name_of_the_package_index_except_the_placeholders_is_accepted() -> None:
    for name in sorted(_index_names() - set(NOT_A_ZONE)):
        assert parse_stored_value(BUSINESS_TIMEZONE, name, "timezone").key == name
