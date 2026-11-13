"""Сторож «бизнес-дата по UTC» (PR #197 check, `NFR-LOC-002`, `T-M2-34`).

Класс дефектов - КАЛЕНДАРНАЯ ДАТА, взятая из системных часов или из UTC там, где нужен
бизнес-день организации, - дал пять мест в `T-M2-34`, и шестое появилось бы так же тихо. Бизнес-день
считает `business_date(utcnow(), zone)` (`core/time_utils.py`): `.date()` там берётся от УЖЕ
ПЕРЕВЕДЁННОГО момента, и именно поэтому он не ловится. Сторож-близнец `browserClockGuard.test.ts`
(фронтенд) и `test_business_rules_not_hardcoded.py` (образец приёма).

ОБНАРУЖЕНИЕ, а не перечисление: разбор дерева (`ast`) каждого модуля `backend/app/**` и поиск
  1. `<часы>.date()`, где часы - вызов `utcnow()`, `now(...)`, `utcnow(...)`, `today()`,
     `fromtimestamp(...)`, `utcfromtimestamp(...)` (`utcnow().date()`, `datetime.now(UTC).date()`);
  2. вызов `.today()` (`date.today()`, `datetime.date.today()`, `datetime.today()`);
  3. `date.fromtimestamp(...)` / `datetime.date.fromtimestamp(...)` / `utcfromtimestamp` у `date`;
  4. SQL: `func.current_date()` и литерал со словом `CURRENT_DATE`;
  5. UTC-дата момента: `<момент>.astimezone(UTC).date()` (в том числе `datetime.UTC`,
     `timezone.utc`) - это календарная дата ПО UTC, пусть и не от системных часов.
Каждый найденный файл обязан стоять в перечне `ALLOWED` С ПРИЧИНОЙ; сегодня перечень НЕ пуст -
см. ниже запись с пометкой «ОТКРЫТО». Перечень проверяется в обе стороны: запись без причины и
запись, в файле которой такого вызова уже нет, краснят сторожа.

НЕ СЛЕПНЕТ: порог - число просмотренных файлов (`MEASURED_FILES`, измерено), краснеет на удалении
модулей.

ГРАНИЦЫ (правило 8). Сторож доказывает отсутствие ЛОВИМЫХ вызовов, а не отсутствие часов вообще.
Он НЕ видит: дату, посчитанную в SQL иначе (`cast(func.now(), Date)`, `now()::date`,
`CURRENT_TIMESTAMP`, `date_trunc`); обёртку вокруг `utcnow()` (`def my_today(): return
utcnow().date()` сработает на самой обёртке, но вызов `my_today()` в другом модуле - нет, как и
вызов функции, которая сама вернёт дату из часов в другом месте); импорт под псевдонимом
(`from datetime import date as d; d.today()`, `from app.core.time_utils import utcnow as n`);
`time.time()` и `time.localtime()`; `datetime.datetime.utcnow()` без `.date()`, сохранённый в
переменную и приведённый к дате отдельной строкой (`now = utcnow(); today = now.date()`).
Литерал `CURRENT_DATE` в строке ловится, в том числе в комментарии-строке и докстринге-константе
(это даёт ложное срабатывание, а не пропуск; докстринги модуля, класса и функции исключены).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent / "app"

# Число просмотренных файлов `backend/app/**/*.py` на момент заведения сторожа (измерено).
MEASURED_FILES = 82

# Адрес (путь файла от `app/`) -> причина, по которой взятие календарной даты здесь верно, ЛИБО
# пометка «ОТКРЫТО» - найдено при заведении сторожа и не решено (решение отдельное, не молчаливая
# правка).
ALLOWED: dict[str, str] = {
    "modules/balances/service.py": (
        "ОТКРЫТО (найдено при заведении сторожа, PR #197 check; в перечне `T-M2-34` этого места "
        "нет): `accrue_all_working_years` берёт `card_date = employee.created_at.astimezone("
        "datetime.UTC).date()` - UTC-дату момента создания карточки - и сравнивает её с датами "
        "рабочих лет (`next_start <= card_date`: год, закончившийся до появления карточки, не "
        "вычисляется, а приходит начальным остатком). Это не системные часы, но календарная дата "
        "ПО UTC, а не по поясу организации (`NFR-LOC-002`): карточка, созданная между 00:00 и "
        "03:00 по Москве, получает вчерашнюю дату (вывод по коду, прогоном не проверялось). "
        "Не исправлено, решается отдельно."
    ),
}

_CLOCK_CALLS = {"utcnow", "now", "today", "fromtimestamp", "utcfromtimestamp"}
_UTC_NAMES = {"UTC", "utc"}
_CURRENT_DATE = re.compile(r"\bCURRENT_DATE\b", re.IGNORECASE)


def _callee(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
    return None


def _is_utc(node: ast.AST) -> bool:
    return (isinstance(node, ast.Name) and node.id in _UTC_NAMES) or (
        isinstance(node, ast.Attribute) and node.attr in _UTC_NAMES
    )


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def find_clock_dates(source: str) -> list[str]:
    """Описания найденных мест (`строка: что`) по правилам 1-5 из докстринга модуля."""
    tree = ast.parse(source)
    skip = _docstrings(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            receiver = node.func.value
            if attr == "date" and _callee(receiver) in _CLOCK_CALLS:
                found.append(f"{node.lineno}: {_callee(receiver)}(...).date()")
            if (
                attr == "date"
                and _callee(receiver) == "astimezone"
                and isinstance(receiver, ast.Call)
                and receiver.args
                and _is_utc(receiver.args[0])
            ):
                found.append(f"{node.lineno}: astimezone(UTC).date()")
            if attr == "today":
                found.append(f"{node.lineno}: .today()")
            if attr in {"fromtimestamp", "utcfromtimestamp"} and (
                (isinstance(receiver, ast.Name) and receiver.id == "date")
                or (isinstance(receiver, ast.Attribute) and receiver.attr == "date")
            ):
                found.append(f"{node.lineno}: date.{attr}(...)")
            if attr == "current_date":
                found.append(f"{node.lineno}: func.current_date()")
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
            and _CURRENT_DATE.search(node.value)
        ):
            found.append(f"{node.lineno}: литерал CURRENT_DATE")
    return found


def _app_files() -> list[Path]:
    return sorted(_APP_ROOT.rglob("*.py"))


def _address(path: Path) -> str:
    return path.relative_to(_APP_ROOT).as_posix()


def test_the_guard_looks_at_the_whole_application_not_at_an_empty_set() -> None:
    assert len(_app_files()) >= MEASURED_FILES


def test_every_clock_date_sits_at_an_allowed_address_with_a_reason() -> None:
    offenders = []
    for path in _app_files():
        calls = find_clock_dates(path.read_text(encoding="utf-8"))
        address = _address(path)
        if calls and not ALLOWED.get(address, "").strip():
            offenders.append(f"{address}: {'; '.join(calls)}")

    assert offenders == []


def test_the_allowed_list_has_no_stale_address() -> None:
    stale = [
        address
        for address in ALLOWED
        if not (_APP_ROOT / address).is_file()
        or not find_clock_dates((_APP_ROOT / address).read_text(encoding="utf-8"))
    ]

    assert stale == []


def test_business_date_itself_is_not_reported() -> None:
    # `.date()` от УЖЕ ПЕРЕВЕДЁННОГО в зону организации момента - это и есть бизнес-день.
    source = (_APP_ROOT / "core" / "time_utils.py").read_text(encoding="utf-8")

    assert find_clock_dates(source) == []


def test_each_detection_rule_fires_on_a_synthetic_source() -> None:
    cases = {
        "x = utcnow().date()": "utcnow(...).date()",
        "x = datetime.datetime.now(datetime.UTC).date()": "now(...).date()",
        "x = datetime.date.today()": ".today()",
        "x = date.today()": ".today()",
        "x = date.fromtimestamp(t)": "date.fromtimestamp(...)",
        "x = datetime.date.fromtimestamp(t)": "date.fromtimestamp(...)",
        "x = datetime.fromtimestamp(t).date()": "fromtimestamp(...).date()",
        "x = func.current_date()": "func.current_date()",
        'q = text("SELECT 1 WHERE d = CURRENT_DATE")': "литерал CURRENT_DATE",
        "x = moment.astimezone(datetime.UTC).date()": "astimezone(UTC).date()",
        "x = moment.astimezone(timezone.utc).date()": "astimezone(UTC).date()",
    }

    for source, expected in cases.items():
        found = find_clock_dates(source)
        assert len(found) == 1 and expected in found[0], (source, found)


def test_calls_that_are_not_a_clock_date_are_not_reported() -> None:
    quiet = [
        "x = moment.astimezone(zone).date()",
        "x = datetime.fromtimestamp(t, tz=UTC).isoformat()",
        "x = cell.date().isoformat()",
        "x = datetime.date(2026, 1, 1)",
        "x = utcnow()",
        '"""Модуль про CURRENT_DATE."""\nx = 1',
    ]

    for source in quiet:
        assert find_clock_dates(source) == [], source
