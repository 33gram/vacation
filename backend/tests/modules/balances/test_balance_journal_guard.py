"""Журнал остатка добавляемый, и остаток считается в одном месте
(T-M2-02, T-M2-08).

Сторож ОБНАРУЖИВАЕТ, а не перечисляет: читает дерево разбора всех модулей
`app/`, находит те, что обращаются к журналу `leave_balance_entries`, и
проверяет их.

  1. К журналу обращается ровно тот набор файлов, что назван в реестре
     `_JOURNAL_FILES` (у каждой записи - причина): модель, её реэкспорт,
     схема ответа (только кортеж типов событий) и сервис
     `modules/balances/service.py`. Любой другой модуль (отчёт,
     начисление, эндпоинт) со своей суммой или вставкой краснит сторожа -
     остаток и запись событий проходят через функции сервиса.
  2. В КАЖДОМ из этих файлов, включая модель, нет путей изменения и
     удаления: вызовов `update`/`delete`/`merge`/`bulk_update_mappings`/
     `setattr`/`truncate`, присваивания атрибуту чужого объекта
     (`entry.quantity = ...`) и строковых литералов с `UPDATE`/`DELETE`/
     `TRUNCATE`. Строки-докстринги (отдельным выражением) не считаются.

ГРАНИЦА (правило 8). Сторож видит имена и литералы, а не поведение:
  - не видит доступ к таблице, имя которой собрано из частей строки или
    взято из конфигурации, и ORM-событие, регистрируемое динамически;
  - присваивание атрибуту ловит по форме `<имя>.<атрибут> = ...`, но не
    `obj[...] = ...` и не изменение через `setattr` под псевдонимом;
  - вызов с именем `update`/`delete` ловит независимо от объекта, поэтому
    безобидный `dict.update` в таком файле тоже даст срабатывание - цена
    простоты, и в файлах журнала его быть не должно.
Запрет на уровне прав БД для роли приложения - `NFR-AUDIT-001`, этап M6;
этот тест его не заменяет.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parents[3] / "app"
# Реестр файлов, которым разрешено обращаться к журналу, с причиной у каждого
# (по образцу `_ALLOWED_WITHOUT_*` из T-M1-58): новая запись видна в диффе и
# требует слова «почему».
_JOURNAL_FILES = {
    "models/leave_balance_entry.py": "объявляет таблицу и модель (схема, не код записи)",
    "models/__init__.py": "реэкспорт модели для Alembic и тестов, обращений к данным нет",
    "schemas/balance.py": (
        "берёт кортеж типов событий `LEAVE_BALANCE_ENTRY_TYPES` для `Literal` схемы ответа "
        "(T-M2-25); данных журнала не читает и не пишет"
    ),
    "modules/balances/service.py": "единственное место записи событий и вычисления остатка",
}
_MODEL_NAME = "LeaveBalanceEntry"
_TABLE_NAME = "leave_balance_entries"
_MODEL_MODULE = "app.models.leave_balance_entry"
_MUTATING_CALLS = {"update", "delete", "merge", "bulk_update_mappings", "setattr", "truncate"}
_MUTATING_SQL = re.compile(r"\b(UPDATE|DELETE|TRUNCATE)\b", re.IGNORECASE)


def _docstrings(tree: ast.AST) -> set[int]:
    """id() строковых литералов, стоящих отдельным выражением (докстринги
    и пояснения): они описывают запрет и не являются кодом."""
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def touches_journal(source: str) -> bool:
    tree = ast.parse(source)
    skip = _docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == _MODEL_NAME:
            return True
        if isinstance(node, ast.Attribute) and node.attr == _MODEL_NAME:
            return True
        if isinstance(node, ast.ImportFrom):
            if node.module == _MODEL_MODULE or any(a.name == _MODEL_NAME for a in node.names):
                return True
        if isinstance(node, ast.Import) and any(a.name == _MODEL_MODULE for a in node.names):
            return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
            and _TABLE_NAME in node.value
        ):
            return True
    return False


def find_mutations(source: str, filename: str) -> list[str]:
    tree = ast.parse(source)
    skip = _docstrings(tree)
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in _MUTATING_CALLS:
                problems.append(f"{filename}:{node.lineno}: вызов {name}")
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id not in {"self", "cls"}
            ):
                problems.append(
                    f"{filename}:{node.lineno}: присваивание {target.value.id}.{target.attr}"
                )
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
            and _MUTATING_SQL.search(node.value)
        ):
            problems.append(f"{filename}:{node.lineno}: SQL-литерал с изменением или удалением")
    return problems


def _journal_files() -> dict[str, str]:
    found = {}
    for path in sorted(_APP_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if touches_journal(source):
            found[path.relative_to(_APP_ROOT).as_posix()] = source
    return found


def test_only_the_balances_service_reaches_the_journal() -> None:
    assert set(_journal_files()) == set(_JOURNAL_FILES)


def test_files_that_reach_the_journal_have_no_update_or_delete_paths() -> None:
    problems = []
    for name, source in _journal_files().items():
        problems += find_mutations(source, name)

    assert problems == []


# --- сторож не пуст: каждый вид нарушения обнаруживается -------------------------


def test_a_module_outside_the_allowed_set_that_sums_the_journal_is_found() -> None:
    source = (
        "from sqlalchemy import func, select\n"
        "from app.models.leave_balance_entry import LeaveBalanceEntry\n"
        "q = select(func.sum(LeaveBalanceEntry.quantity))\n"
    )

    assert touches_journal(source)


def test_a_module_that_reaches_the_table_by_name_is_found() -> None:
    assert touches_journal(
        'rows = await session.execute(text("SELECT * FROM leave_balance_entries"))'
    )


def test_a_module_that_never_mentions_the_journal_is_not_found() -> None:
    assert not touches_journal("from app.models.leave_type import LeaveType\nx = LeaveType\n")


def test_a_docstring_that_names_the_table_is_not_a_reference() -> None:
    assert not touches_journal('"""Читает leave_balance_entries."""\nx = 1\n')


def test_each_kind_of_update_or_delete_is_reported() -> None:
    cases = {
        "update(": "await session.execute(update(LeaveBalanceEntry).values(quantity=1))\n",
        "delete(": "await session.execute(delete(LeaveBalanceEntry))\n",
        "session.delete": "await session.delete(entry)\n",
        "merge": "await session.merge(entry)\n",
        "attribute assignment": "entry.quantity = 5\n",
        "augmented assignment": "entry.quantity += 5\n",
        "setattr": "setattr(entry, 'reason', 'x')\n",
        "raw UPDATE": 'await session.execute(text("UPDATE leave_balance_entries SET q = 1"))\n',
        "raw delete": 'await session.execute(text("delete from leave_balance_entries"))\n',
    }
    for label, source in cases.items():
        assert find_mutations(source, "x.py"), label


def test_inserting_an_entry_is_not_a_mutation() -> None:
    source = (
        "entry = LeaveBalanceEntry(employee_id=1, quantity=2)\n"
        "session.add(entry)\n"
        "await session.flush()\n"
    )

    assert find_mutations(source, "x.py") == []


def test_a_docstring_that_says_update_is_not_a_mutation() -> None:
    assert find_mutations('"""Путей UPDATE и DELETE нет."""\nx = 1\n', "x.py") == []
