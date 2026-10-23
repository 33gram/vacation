"""Сидирование `business_rules_config` (T-M2-03, раздел 5, `FR-ADMIN-004`).

Закрывает ровно то, что закрывает T-M2-03: параметры хранятся и
предустановлены. Чтение/проверка значений — T-M2-09, эндпоинты и интерфейс
— T-M2-12/T-M2-15; здесь они не проверяются.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from sqlalchemy import text

_MIGRATION = (
    Path(__file__).resolve().parents[3] / "alembic" / "versions" / "0007_business_rules_config.py"
)

# Ожидаемое содержимое таблицы после миграции — ВСЕ ключи, записанные здесь
# независимо от миграции: любое изменение сидирования обязано покраснеть
# (пометка ВЫВЕДЕНО/ВЫБРАНО в миграции объясняет происхождение значения, а
# не степень его защищённости). Ключ -> (значение, тип значения).
_EXPECTED = {
    "annual_leave_days": ("28", "integer"),
    "first_leave_eligibility_months": ("6", "integer"),
    "schedule_approval_deadline": ("12-17", "month_day"),
    "vacation_start_notice_days": ("14", "integer"),
    "min_leave_part_days": ("14", "integer"),
    "leave_usage_deadline_months": ("12", "integer"),
    "compensation_threshold_days": ("28", "integer"),
    "approval_escalation_working_days": ("3", "integer"),
    "password_min_length": ("8", "integer"),
    "max_failed_login_attempts": ("5", "integer"),
    "lockout_duration_minutes": ("15", "integer"),
    # 0009 (T-M2-05)
    "month_rounding_threshold_days": ("15", "integer"),
    "unpaid_leave_days_counted_in_service": ("14", "integer"),
}


def _load_seed():
    spec = importlib.util.spec_from_file_location("migration_0007", _MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.seed_business_rules


async def _rows(db_session) -> dict[str, dict]:
    result = await db_session.execute(text("SELECT * FROM business_rules_config"))
    return {row["key"]: dict(row) for row in result.mappings()}


async def _run_seed(db_session) -> None:
    seed = _load_seed()
    await db_session.run_sync(lambda session: seed(session.connection()))


async def test_every_seeded_parameter_matches_the_expected_table(db_session) -> None:
    rows = await _rows(db_session)

    actual = {key: (row["value"], row["value_type"]) for key, row in rows.items()}
    assert actual == _EXPECTED


async def test_seeded_rows_are_untouched_by_anyone_and_carry_a_source_citation(db_session) -> None:
    rows = await _rows(db_session)

    assert all(row["updated_by_account_id"] is None for row in rows.values())
    assert all(row["updated_at"] is not None for row in rows.values())
    # Цитата источника: хотя бы один идентификатор BR-n/FR-XXX-NNN.
    citation = re.compile(r"\b(BR-\d+|FR-[A-Z]+-\d+)\b")
    assert [key for key, row in rows.items() if not citation.search(row["description"])] == []


async def test_running_the_seed_again_creates_no_duplicates(db_session) -> None:
    await _run_seed(db_session)
    await _run_seed(db_session)

    result = await db_session.execute(
        text("SELECT count(*), count(DISTINCT key) FROM business_rules_config")
    )
    assert result.one() == (len(_EXPECTED), len(_EXPECTED))


async def test_running_the_seed_again_does_not_overwrite_an_admin_edit(db_session) -> None:
    await db_session.execute(
        text(
            "UPDATE business_rules_config SET value = '30', description = 'Правка администратора' "
            "WHERE key = 'annual_leave_days'"
        )
    )

    await _run_seed(db_session)

    row = (await _rows(db_session))["annual_leave_days"]
    assert (row["value"], row["description"]) == ("30", "Правка администратора")


async def test_running_the_seed_again_restores_a_missing_key(db_session) -> None:
    # Свойство ОПЕРАТОРА, не миграции: вставка возвращает отсутствующий ключ
    # (иначе пополняющая миграция не смогла бы добавить параметр). Что
    # удалённая администратором строка не воскресает при `upgrade head`,
    # обеспечивает Alembic (ревизия применяется один раз), а не этот оператор.
    await db_session.execute(
        text("DELETE FROM business_rules_config WHERE key = 'min_leave_part_days'")
    )

    await _run_seed(db_session)

    assert (await _rows(db_session))["min_leave_part_days"]["value"] == "14"
