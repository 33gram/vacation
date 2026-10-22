"""Сторож соответствия: правила `BR-*` раздела 5 `docs/requirements.md`
против ключей `business_rules_config` (T-M2-03).

Перечень правил берётся ИЗ ДОКУМЕНТА, а не из этого файла: правило с
числовым значением, добавленное в раздел 5 без записи в `_BR_KEYS`,
краснит сторожа, а не теряется. Явный здесь только словарь «правило ->
ключи» — правка видна в дифе, по образцу `test_leave_types_section6_guard.py`.
Ключи берутся из `_PRESET` ВСЕХ миграций, сидирующих `business_rules_config`
(обнаруживаются в `alembic/versions`, а не перечислены; не из БД), чтобы
сторож не зависел от состояния базы.

Граница (правило 8): сторож подтверждает, что для каждого правила с числом
ключ СУЩЕСТВУЕТ и его `description` называет правило, а не что значение
ключа равно числу в тексте (это закрепляет `test_seed.py`).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REQUIREMENTS_PATH = _REPO_ROOT / "docs" / "requirements.md"
# Миграции ОБНАРУЖИВАЮТСЯ, а не перечисляются: ключи, добавленные любой будущей
# миграцией (0010 и далее), попадают под сторожа без правки этого файла.
_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
_TABLE = "business_rules_config"
_DECLARES_TABLE = re.compile(r"sa\.table\(\s*[\"']" + _TABLE + r"[\"']")

pytestmark = pytest.mark.skipif(
    not _REQUIREMENTS_PATH.is_file(),
    reason=(
        "docs/requirements.md не резолвится по вычисленному пути "
        "(parents[2] от backend/tests) - та же причина, что у "
        "test_leave_types_section6_guard.py: внутри dev-контейнера backend "
        "смонтировано только поддерево backend/; в CI путь резолвится верно"
    ),
)

# Правило раздела 5, содержащее число -> ключи, в которые оно вошло.
_BR_KEYS: dict[str, tuple[str, ...]] = {
    "BR-1": ("annual_leave_days",),
    "BR-2": ("first_leave_eligibility_months",),
    # «за 2 недели до начала года (не позднее 17 декабря)» - один ключ-дата:
    # «2 недели» и «17 декабря» - одно правило, а не два числа (см. миграцию).
    "BR-3": ("schedule_approval_deadline",),
    "BR-4": ("vacation_start_notice_days",),
    "BR-5": ("min_leave_part_days",),
    "BR-6": ("leave_usage_deadline_months",),
    "BR-7": ("compensation_threshold_days",),
}

# Ключи, источник которых - не раздел 5, а FR-* (идентификатор должен
# совпасть с цитатой в `description`).
_FR_KEYS: dict[str, str] = {
    "approval_escalation_working_days": "FR-APPR-004",
    "password_min_length": "FR-AUTH-002/2",
    "max_failed_login_attempts": "FR-AUTH-002/2",
    "lockout_duration_minutes": "FR-AUTH-002/2",
    "month_rounding_threshold_days": "FR-BAL-001/1",
    "unpaid_leave_days_counted_in_service": "FR-BAL-001/2",
}

# Ссылки на статьи/законы/годы - тоже цифры, но не значения параметров.
_NOT_A_VALUE = re.compile(r"ст\.\s*\d+|№\s*\d+(?:-ФЗ)?|\b\d{4}\s*года?\b")


def _section5_rules(requirements_text: str) -> dict[str, str]:
    section = re.search(
        r"^## 5\. Бизнес-правила \(нормативная база\)\n(.*?)(?=^## )",
        requirements_text,
        re.M | re.S,
    )
    assert section, "в requirements.md не найден раздел 5"
    rules = {}
    for line in section.group(1).splitlines():
        match = re.match(r"\|\s*(BR-\d+)\s*\|(.*)\|\s*$", line)
        if match:
            rules[match.group(1)] = match.group(2)
    assert rules, "в разделе 5 не найдена таблица правил BR-*"
    return rules


def _numeric_rules(requirements_text: str) -> set[str]:
    return {
        rule
        for rule, body in _section5_rules(requirements_text).items()
        if re.search(r"\d", _NOT_A_VALUE.sub("", body))
    }


def _preset(versions_dir: Path = _VERSIONS) -> dict[str, str]:
    """Ключ -> description из `_PRESET` каждой миграции, которая сидирует
    `business_rules_config`. Такая миграция узнаётся по `_BUSINESS_RULES`
    (таблица с этим именем), а не по номеру: `_PRESET` есть и у `0006`, но
    там виды отпуска. Пустота и непрочитанная миграция - громкий отказ, а не
    проход на пустом множестве."""
    descriptions: dict[str, str] = {}
    found: list[str] = []
    for path in sorted(versions_dir.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"guard_{path.stem}", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        table = getattr(module, "_BUSINESS_RULES", None)
        preset = getattr(module, "_PRESET", None)
        if table is not None and table.name == _TABLE and preset is not None:
            found.append(path.name)
            descriptions.update({row["key"]: row["description"] for row in preset})
        elif _DECLARES_TABLE.search(path.read_text(encoding="utf-8")):
            raise AssertionError(
                f"{path.name} объявляет таблицу {_TABLE}, но сторож не нашёл в ней "
                "`_BUSINESS_RULES` и `_PRESET` - миграция названа иначе, и её ключи "
                "остались бы без проверки"
            )
    assert found, f"сторож ослеп: ни одной миграции с `_PRESET` для {_TABLE} в {versions_dir}"
    return descriptions


def _names(description: str, identifier: str) -> bool:
    """`description` называет именно этот идентификатор: после него не
    идёт символ идентификатора, иначе `BR-1` совпал бы с `BR-12`, а
    `FR-AUTH-002/2` - с `FR-AUTH-002/20`. Перед ним - не буква, не цифра и
    не `-`, иначе `XBR-1` сошёлся бы с `BR-1` (PR #176 review, С-7)."""
    return re.search(rf"(?<![\w-]){re.escape(identifier)}(?!\w)", description) is not None


def find_gaps(requirements_text: str, preset: dict[str, str]) -> list[str]:
    numeric = _numeric_rules(requirements_text)
    problems = [
        f"правило раздела 5 с числом без отображения в ключи: {rule!r}"
        for rule in sorted(numeric - set(_BR_KEYS), key=lambda r: int(r[3:]))
    ]
    problems += [
        f"отображение для правила, которого нет среди правил с числом: {rule!r}"
        for rule in _BR_KEYS
        if rule not in numeric
    ]
    for rule, keys in _BR_KEYS.items():
        for key in keys:
            if key not in preset:
                problems.append(f"правило {rule}: ключа {key!r} нет в сидировании")
            elif not _names(preset[key], rule):
                problems.append(f"ключ {key!r}: description не называет {rule}")
    for key, source in _FR_KEYS.items():
        if key not in preset:
            problems.append(f"ключа {key!r} ({source}) нет в сидировании")
        elif not _names(preset[key], source):
            problems.append(f"ключ {key!r}: description не называет {source}")
    mapped = {key for keys in _BR_KEYS.values() for key in keys} | set(_FR_KEYS)
    problems += [
        f"ключ {key!r} в сидировании не отображён ни в BR-*, ни в FR-*"
        for key in sorted(set(preset) - mapped)
    ]
    return problems


def test_every_section5_rule_with_a_number_has_a_seeded_key() -> None:
    text = _REQUIREMENTS_PATH.read_text(encoding="utf-8")

    assert find_gaps(text, _preset()) == []


def test_rule_with_a_number_added_to_section5_is_reported() -> None:
    text = _REQUIREMENTS_PATH.read_text(encoding="utf-8")
    anchor = "| BR-8 |"
    assert anchor in text
    extended = text.replace(anchor, "| BR-12 | Новое правило | Не менее 5 дней. |\n" + anchor, 1)

    assert find_gaps(extended, _preset()) == [
        "правило раздела 5 с числом без отображения в ключи: 'BR-12'"
    ]


def test_rule_without_a_number_needs_no_key() -> None:
    text = _REQUIREMENTS_PATH.read_text(encoding="utf-8")
    anchor = "| BR-8 |"
    extended = text.replace(
        anchor, "| BR-12 | Новое правило | По ст. 130 ТК РФ, без срока. |\n" + anchor, 1
    )

    assert find_gaps(extended, _preset()) == []


def test_missing_seeded_key_is_reported() -> None:
    text = _REQUIREMENTS_PATH.read_text(encoding="utf-8")
    preset = _preset()
    del preset["min_leave_part_days"]

    assert find_gaps(text, preset) == [
        "правило BR-5: ключа 'min_leave_part_days' нет в сидировании"
    ]


def test_unmapped_seeded_key_is_reported() -> None:
    text = _REQUIREMENTS_PATH.read_text(encoding="utf-8")

    problems = find_gaps(text, {**_preset(), "surprise": "BR-1"})

    assert problems == ["ключ 'surprise' в сидировании не отображён ни в BR-*, ни в FR-*"]


def test_description_that_loses_its_source_is_reported() -> None:
    text = _REQUIREMENTS_PATH.read_text(encoding="utf-8")

    problems = find_gaps(text, {**_preset(), "annual_leave_days": "Без ссылки."})

    assert problems == ["ключ 'annual_leave_days': description не называет BR-1"]


def test_description_naming_a_longer_rule_id_does_not_count_as_naming_the_rule() -> None:
    text = _REQUIREMENTS_PATH.read_text(encoding="utf-8")
    extended = text.replace(
        "| BR-8 |", "| BR-12 | Новое правило | Не менее 5 дней. |\n| BR-8 |", 1
    )

    problems = find_gaps(extended, {**_preset(), "annual_leave_days": "BR-12: другое правило."})

    # Именно BR-12 (правило без ключа) и именно BR-1 (описание его не называет);
    # подстрочное сравнение промолчало бы про второе.
    assert problems == [
        "правило раздела 5 с числом без отображения в ключи: 'BR-12'",
        "ключ 'annual_leave_days': description не называет BR-1",
    ]


def test_description_naming_a_longer_fr_criterion_does_not_count_as_naming_the_source() -> None:
    text = _REQUIREMENTS_PATH.read_text(encoding="utf-8")

    problems = find_gaps(text, {**_preset(), "password_min_length": "FR-AUTH-002/20: иное."})

    assert problems == ["ключ 'password_min_length': description не называет FR-AUTH-002/2"]


def test_description_naming_the_rule_followed_by_punctuation_counts() -> None:
    assert _names("BR-1: норма.", "BR-1")
    assert _names("см. (BR-1)", "BR-1")
    assert not _names("XBR-1: норма.", "BR-1")


def test_discovery_finds_the_keys_of_both_seeding_migrations() -> None:
    # Отрицательный контроль обнаружения: по ключу из 0007 и из 0009. Без него
    # "обнаружение работает" проходило бы и на неполном наборе.
    preset = _preset()

    assert "annual_leave_days" in preset  # 0007
    assert "month_rounding_threshold_days" in preset  # 0009


def test_discovery_does_not_take_the_presets_of_other_tables() -> None:
    # `0006._PRESET` - виды отпуска (словари без `description`-ключа правил).
    assert "annual_paid" not in _preset()


def test_a_guard_that_finds_no_preset_fails_loudly(tmp_path) -> None:
    (tmp_path / "0001_nothing.py").write_text("X = 1\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="сторож ослеп"):
        _preset(tmp_path)


def test_a_migration_that_declares_the_table_under_other_names_is_reported(tmp_path) -> None:
    (tmp_path / "0010_renamed.py").write_text(
        'import sqlalchemy as sa\nRULES = sa.table("business_rules_config", sa.column("key"))\n',
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="0010_renamed.py"):
        _preset(tmp_path)
