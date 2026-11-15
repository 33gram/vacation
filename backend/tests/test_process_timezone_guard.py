"""Сторож «пояс процесса только в планировщике» (PR #199 review, `T-M2-39`, `NFR-LOC-002`).

Решение заказчика: пояс ПРОЦЕССА (`settings.tz`, переменная `TZ`) задаёт только момент
срабатывания расписаний Celery, а бизнес-день берётся из `business_timezone` (`business_today`,
`business_date_of`). Сторож `test_no_utc_business_date_guard.py` ловит ЧАСЫ (`now(...).date()`),
но не ловит самый естественный способ нарушить решение:
`utcnow().astimezone(ZoneInfo(settings.tz)).date()` или `now = ...; now.date()` в две строки
(измерено ревью). Этот сторож смотрит на ИСТОЧНИК пояса, а не на форму вызова: обращение к
`settings.tz` в `backend/app/**` допустимо только там, где оно записано в `ALLOWED`.

ОБНАРУЖЕНИЕ (`ast`, весь `backend/app/**`):
  1. атрибут `.tz` у любого получателя (`settings.tz`, `get_settings().tz`, `cfg.tz`);
  2. строковый литерал ровно `"TZ"` вне докстрингов (`os.environ["TZ"]`, `os.getenv("TZ")`).
Определение поля `tz: str = ...` в `core/config.py` - присваивание имени, не атрибут: не ловится.

ИСКЛЮЧЕНИЯ - `ALLOWED`: ключ `файл::текст выражения` (`ast.unparse`), значение - (сколько раз,
причина); проверка в обе стороны, порог числа файлов - как у сторожа-близнеца.

ГРАНИЦЫ (правило 8). Не ловится: `getattr(settings, "tz")`; имя поля, переданное строкой в обход
атрибута (`settings.model_dump()["tz"]`); чтение `TZ` не по литералу (`os.environ[name]`);
`datetime.now().astimezone()` без аргумента (пояс системы, а не `settings.tz`) - это класс
сторожа часов, и он ловит только форму `.date()`. Сторож не доказывает, что планировщик СЧИТАЕТ
дату верно, - только что пояс процесса не просачивается в другой модуль.
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent / "app"

# Число просмотренных файлов `backend/app/**/*.py` на момент заведения сторожа (измерено).
MEASURED_FILES = 82

ALLOWED: dict[str, tuple[int, str]] = {
    "workers/celery_app.py::settings.tz": (
        1,
        "T-M2-39: пояс процесса задаёт ТОЛЬКО момент срабатывания расписаний Celery "
        "(`celery_app.conf.timezone`); бизнес-день берётся из `business_timezone`",
    ),
}


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def find_process_zone(source: str) -> list[tuple[int, str, str]]:
    """Найденные места по правилам 1-2 из докстринга модуля: (строка, правило, текст выражения)."""
    tree = ast.parse(source)
    skip = _docstrings(tree)
    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "tz":
            found.append((node.lineno, ".tz", ast.unparse(node)))
        if isinstance(node, ast.Constant) and node.value == "TZ" and id(node) not in skip:
            found.append((node.lineno, 'литерал "TZ"', "TZ"))
    return found


def _app_files() -> list[Path]:
    return sorted(_APP_ROOT.rglob("*.py"))


def _address(path: Path) -> str:
    return path.relative_to(_APP_ROOT).as_posix()


def _found_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in _app_files():
        for _line, _rule, text in find_process_zone(path.read_text(encoding="utf-8")):
            key = f"{_address(path)}::{text}"
            counts[key] = counts.get(key, 0) + 1
    return counts


def _offenders(found: dict[str, int], allowed: dict[str, tuple[int, str]]) -> list[str]:
    offenders = []
    for key, count in sorted(found.items()):
        permitted, reason = allowed.get(key, (0, ""))
        if count > permitted or not reason.strip():
            offenders.append(f"{key} (найдено {count}, разрешено {permitted})")
    return offenders


def _stale(found: dict[str, int], allowed: dict[str, tuple[int, str]]) -> list[str]:
    return [key for key, (count, _reason) in allowed.items() if found.get(key, 0) < count]


def test_the_guard_looks_at_the_whole_application_not_at_an_empty_set() -> None:
    assert len(_app_files()) >= MEASURED_FILES


def test_the_process_zone_is_read_only_where_it_is_allowed() -> None:
    assert _offenders(_found_counts(), ALLOWED) == []


def test_the_allowed_list_has_no_stale_entry() -> None:
    assert _stale(_found_counts(), ALLOWED) == []


def test_the_process_zone_is_read_exactly_once_outside_the_settings_module() -> None:
    # Измерено при заведении: единственное обращение - `celery_app.conf.timezone = settings.tz`.
    assert _found_counts() == {"workers/celery_app.py::settings.tz": 1}


def test_each_detection_rule_fires_on_a_synthetic_source() -> None:
    cases = {
        "x = settings.tz": ".tz",
        "x = ZoneInfo(settings.tz)": ".tz",
        "x = utcnow().astimezone(ZoneInfo(settings.tz)).date()": ".tz",
        "x = get_settings().tz": ".tz",
        'x = os.environ["TZ"]': 'литерал "TZ"',
        'x = os.getenv("TZ")': 'литерал "TZ"',
    }

    for source, expected in cases.items():
        found = find_process_zone(source)
        assert len(found) == 1 and found[0][1] == expected, (source, found)


def test_the_field_definition_and_docstrings_are_not_reported() -> None:
    config = (_APP_ROOT / "core" / "config.py").read_text(encoding="utf-8")

    assert find_process_zone(config) == []
    assert find_process_zone('"""Про TZ."""\n\ndef f():\n    """TZ"""\n') == []


def test_a_second_read_or_a_new_file_is_offending() -> None:
    found = {"workers/celery_app.py::settings.tz": 2, "modules/x.py::ZoneInfo(settings.tz)": 1}

    assert _offenders(found, ALLOWED) == [
        "modules/x.py::ZoneInfo(settings.tz) (найдено 1, разрешено 0)",
        "workers/celery_app.py::settings.tz (найдено 2, разрешено 1)",
    ]
    no_reason = {"workers/celery_app.py::settings.tz": (1, " ")}
    assert _offenders({"workers/celery_app.py::settings.tz": 1}, no_reason) != []
    assert _stale({}, ALLOWED) == ["workers/celery_app.py::settings.tz"]
