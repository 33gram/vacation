"""Сторож «бизнес-дата по UTC» (PR #197 check/review, `NFR-LOC-002`, `T-M2-34`).

Класс дефектов - КАЛЕНДАРНАЯ ДАТА, взятая из системных часов или из UTC там, где нужен бизнес-день
организации, - дал пять мест в `T-M2-34`, и шестое появилось бы так же тихо. Бизнес-день
считает `business_date(utcnow(), zone)` (`core/time_utils.py`): `.date()` там берётся от УЖЕ
ПЕРЕВЕДЁННОГО момента, и именно поэтому он не ловится. Сторож-близнец `browserClockGuard.test.ts`
(фронтенд) и `test_business_rules_not_hardcoded.py` (образец приёма).

ОБНАРУЖЕНИЕ, а не перечисление: разбор дерева (`ast`) каждого модуля `backend/app/**` и поиск
  1. `<часы>.date()`, где часы - вызов `utcnow()`, `now(...)`, `utcnow(...)`, `today()`,
     `fromtimestamp(...)`, `utcfromtimestamp(...)` (`utcnow().date()`, `datetime.now(UTC).date()`);
  2. ЛЮБОЙ вызов `.today()` (`date.today()`, `datetime.date.today()`, `datetime.today()`, а также
     `d.today()` под псевдонимом импорта - правило смотрит на имя метода, не на получателя);
  3. `date.fromtimestamp(...)` / `datetime.date.fromtimestamp(...)` / `utcfromtimestamp` у `date`
     (получатель - имя `date` или атрибут `.date`);
  4. SQL: `func.current_date()` и литерал со словом `CURRENT_DATE`;
  5. UTC-дата момента: `<момент>.astimezone(UTC).date()` (в том числе `datetime.UTC`,
     `timezone.utc`) - это календарная дата ПО UTC, пусть и не от системных часов.

ИСКЛЮЧЕНИЯ - `ALLOWED`: ключ `файл::текст вызова` (`ast.unparse` найденного выражения, НЕ номер
строки: номера плывут), значение - (сколько раз, причина). Исключение покрывает ТОЛЬКО этот вызов и
ТОЛЬКО это число повторов: второй, новый вызов в том же файле (другой текст или тот же текст сверх
числа) краснит сторожа (раньше запись действовала на весь файл - измерено ревью PR #197). Проверка в
обе стороны: найденный вызов вне перечня, запись без причины, запись, вызов которой в файле не
найден или найден иное число раз - всё краснит. Ограничение: два РАЗНЫХ места файла с одинаковым
текстом вызова различаются только числом повторов, не расположением.

НЕ СЛЕПНЕТ: порог - число просмотренных файлов (`MEASURED_FILES`, измерено), краснеет на удалении
модулей.

ГРАНИЦЫ (правило 8). Сторож доказывает отсутствие ЛОВИМЫХ вызовов, а не отсутствие часов вообще.
Он НЕ видит: дату, посчитанную в SQL иначе (`cast(func.now(), Date)`, `now()::date`,
`CURRENT_TIMESTAMP`, `date_trunc`); обёртку вокруг `utcnow()` (`def my_today(): return
utcnow().date()` сработает на самой обёртке, но вызов `my_today()` в другом модуле - нет, как и
вызов функции, которая сама вернёт дату из часов в другом месте); импорт часов под псевдонимом
(`from app.core.time_utils import utcnow as n; n().date()`, `from datetime import date as d;
d.fromtimestamp(t)` - получатель не `date`); `d.today()` под псевдонимом СТОРОЖ ЛОВИТ (правило 2;
измерено прогоном, раньше здесь утверждалось обратное); `time.time()` и `time.localtime()`;
`now = utcnow(); today = now.date()` в две строки. Литерал `CURRENT_DATE` в строке-константе ловится
(ложное срабатывание, а не пропуск; докстринги модуля, класса и функции исключены).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent / "app"

# Число просмотренных файлов `backend/app/**/*.py` на момент заведения сторожа (измерено).
MEASURED_FILES = 82

# `файл::текст вызова` -> (сколько раз найден, причина). Причина называет решение или задачу;
# запись без причины недопустима.
ALLOWED: dict[str, tuple[int, str]] = {
    "modules/balances/service.py::employee.created_at.astimezone(datetime.UTC).date()": (
        1,
        "T-M2-40 (найдено при заведении сторожа, не из перечня T-M2-34): "
        "`accrue_all_working_years` берёт `card_date` - UTC-дату сохранённой МЕТКИ "
        "времени создания карточки - и сравнивает с датами рабочих лет "
        "(`next_start <= card_date`: пропускается ли ЦЕЛЫЙ рабочий год). Механизм "
        "иной, чем у пяти мест T-M2-34: метка переведена в дату не в поясе "
        "организации. Закреплено намеренно тестом "
        "`test_barrier_the_card_date_is_taken_in_utc_not_in_the_local_zone` "
        "(T-M2-19, до `NFR-LOC-002`); решение - в T-M2-40, не молчаливая правка.",
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


def find_clock_dates(source: str) -> list[tuple[int, str, str]]:
    """Найденные места по правилам 1-5 из докстринга модуля: (строка, правило, текст вызова)."""
    tree = ast.parse(source)
    skip = _docstrings(tree)
    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            receiver = node.func.value
            text = ast.unparse(node)
            if attr == "date" and _callee(receiver) in _CLOCK_CALLS:
                found.append((node.lineno, f"{_callee(receiver)}(...).date()", text))
            if (
                attr == "date"
                and _callee(receiver) == "astimezone"
                and isinstance(receiver, ast.Call)
                and receiver.args
                and _is_utc(receiver.args[0])
            ):
                found.append((node.lineno, "astimezone(UTC).date()", text))
            if attr == "today":
                found.append((node.lineno, ".today()", text))
            if attr in {"fromtimestamp", "utcfromtimestamp"} and (
                (isinstance(receiver, ast.Name) and receiver.id == "date")
                or (isinstance(receiver, ast.Attribute) and receiver.attr == "date")
            ):
                found.append((node.lineno, f"date.{attr}(...)", text))
            if attr == "current_date":
                found.append((node.lineno, "func.current_date()", text))
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
            and _CURRENT_DATE.search(node.value)
        ):
            found.append((node.lineno, "литерал CURRENT_DATE", node.value.strip()[:60]))
    return found


def _app_files() -> list[Path]:
    return sorted(_APP_ROOT.rglob("*.py"))


def _address(path: Path) -> str:
    return path.relative_to(_APP_ROOT).as_posix()


def _found_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in _app_files():
        for _line, _rule, text in find_clock_dates(path.read_text(encoding="utf-8")):
            key = f"{_address(path)}::{text}"
            counts[key] = counts.get(key, 0) + 1
    return counts


def test_the_guard_looks_at_the_whole_application_not_at_an_empty_set() -> None:
    assert len(_app_files()) >= MEASURED_FILES


def test_every_clock_date_is_allowed_exactly_and_with_a_reason() -> None:
    offenders = []
    for key, count in sorted(_found_counts().items()):
        allowed, reason = ALLOWED.get(key, (0, ""))
        if count > allowed or not reason.strip():
            offenders.append(f"{key} (найдено {count}, разрешено {allowed})")

    assert offenders == []


def test_the_allowed_list_has_no_stale_entry() -> None:
    found = _found_counts()
    stale = [key for key, (count, _reason) in ALLOWED.items() if found.get(key, 0) < count]

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
        "from datetime import date as d\nx = d.today()": ".today()",  # псевдоним ловится
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
        assert len(found) == 1 and found[0][1] == expected, (source, found)


def test_calls_that_are_not_a_clock_date_are_not_reported() -> None:
    quiet = [
        "x = moment.astimezone(zone).date()",
        "x = datetime.fromtimestamp(t, tz=UTC).isoformat()",
        "x = cell.date().isoformat()",
        "x = datetime.date(2026, 1, 1)",
        "x = utcnow()",
        # Известные ГРАНИЦЫ (не ловится): псевдоним часов и `fromtimestamp` под псевдонимом `date`.
        "from m import utcnow as n\nx = n().date()",
        "from datetime import date as d\nx = d.fromtimestamp(t)",
        '"""Модуль про CURRENT_DATE."""\nx = 1',
    ]

    for source in quiet:
        assert find_clock_dates(source) == [], source


def test_an_exception_covers_only_its_own_call_not_the_whole_file() -> None:
    # Измерено ревью PR #197: запись на файл пропускала `zz = date.today()` в том же файле.
    key = "modules/balances/service.py::employee.created_at.astimezone(datetime.UTC).date()"
    source = (
        "employee.created_at.astimezone(datetime.UTC).date()\n"
        "zz = datetime.date.today()\n"
        "employee.created_at.astimezone(datetime.UTC).date()\n"
    )
    counts: dict[str, int] = {}
    for _line, _rule, text in find_clock_dates(source):
        full = f"modules/balances/service.py::{text}"
        counts[full] = counts.get(full, 0) + 1

    assert counts[key] == 2  # тот же текст сверх разрешённого числа повторов (1) - краснит
    assert (
        counts["modules/balances/service.py::datetime.date.today()"] == 1
    )  # новый вызов - краснит
    assert ALLOWED[key][0] == 1
