"""Перечень префиксов полей на клиенте совпадает с тем, что размечает бэкенд (`T-M2-27`).

`FIELD_ERROR_PREFIXES` в `frontend/src/api/errors.ts` - закрытый список полей, ошибки которых
интерфейс кладёт под поле формы по префиксу `"<поле>: "` в тексте отказа (`CLAUDE.md`
«Соглашения»). Раньше список сверялся с бэкендом разбором дерева `raise` вручную (`T-M1-20`
review); теперь это делает тест, в обе стороны:
  - в `app/**` есть строковый литерал с таким префиксом, а в перечне записи нет;
  - запись перечня без такого литерала в `app/**`.

Обнаружение - разбор AST (`ast`), не построчный `grep`: строка отказа может стоять на следующей
строке после `raise` или быть собрана из соседних литералов.

ЧТО ДОКАЗЫВАЕТ СВЕРКА (правило 8): НАЛИЧИЕ ЛИТЕРАЛА с префиксом в `app/**`, а не то, что он уходит
в `detail` отказа API. Литерал может стоять в логе, в `Field(description=...)`, в константе без
читателя - сверка его засчитает так же.

ФАКТИЧЕСКОЕ ПРАВИЛО обнаружения: засчитывается любой строковый литерал в `app/**`, к которому
подходит `^слово: `, кроме докстрингов модуля, класса и функции. В том числе ЛЮБОЙ сегмент
f-строки, не только начальный (`ast.walk` отдаёт сегменты `JoinedStr` как отдельные `Constant`).
Соседние литералы (`"a: " "b"`) склеивает сам разбор Python. Правила проверены на синтетическом
дереве (`test_the_discovery_rules_on_a_synthetic_tree`), а не только на `app/**`.
Не видит: префикс, собранный во время выполнения (`f"{key}: {text}"` в `RuleValuesInvalidError` -
ключи реестра разбирает `rulesErrorsByKey` по ключам из ответа `GET`, не этот перечень),
конкатенацию `"поле" + ": текст"`, префикс не в начале литерала, текст из внешнего файла.
Строка вида `слово: ` в `app/**`, которая не префикс поля (`_NOT_FIELD_PREFIXES`), называется
явно с причиной. Что смонтировано в dev-контейнер `backend` - `tests/dev_container.py`:
`frontend/src` там нет, поэтому пропускаются только два теста сверки с перечнем клиента
(`_needs_client_list`), пока каталог не скопирован туда вручную; остальные читают `app/` и
временный каталог и идут всегда. В CI выполняются все.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.dev_container import DEV_CONTAINER_MOUNTS

_APP = Path(__file__).resolve().parents[1] / "app"
_ERRORS_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "errors.ts"

_needs_client_list = pytest.mark.skipif(
    not _ERRORS_TS.is_file(),
    reason=(
        "frontend/src не резолвится по вычисленному пути: "
        + DEV_CONTAINER_MOUNTS
        + "; в CI путь резолвится"
    ),
)

_PREFIX = re.compile(r"^([a-z][a-z0-9_]*): ")

# Строки `слово: ` в `app/**`, которые не префикс поля. Каждая названа с причиной.
_NOT_FIELD_PREFIXES: dict[str, str] = {
    "unreachable": "текст `AssertionError` для недостижимой ветки (`import_service`), не отказ API",
}


def _client_list() -> set[str]:
    source = _ERRORS_TS.read_text("utf-8")
    match = re.search(r"FIELD_ERROR_PREFIXES\s*=\s*\[(.*?)\]\s*as const", source, re.DOTALL)
    assert match, "в errors.ts нет перечня FIELD_ERROR_PREFIXES"
    names = re.findall(r'"([a-z][a-z0-9_]*)"', match.group(1))
    assert len(names) == len(set(names)), "в перечне повторы"
    return set(names)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def _backend_prefixes(root: Path = _APP) -> dict[str, list[str]]:
    """Префикс -> где найден (файл:строка), по правилу из докстринга модуля."""
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in docstrings:
                continue
            match = _PREFIX.match(node.value)
            if match:
                where = f"{path.relative_to(root.parent)}:{node.lineno}"
                found.setdefault(match.group(1), []).append(where)
    return found


def _fields_found() -> dict[str, list[str]]:
    return {k: v for k, v in _backend_prefixes().items() if k not in _NOT_FIELD_PREFIXES}


def test_the_discovery_is_not_blind() -> None:
    # Приметы, которые нашлись бы при любом работающем обнаружении.
    found = _fields_found()
    assert "personnel_number" in found  # литерал в f-строке внутри помощника
    assert "manager_id" in found  # литерал в `raise ValueError(...)`
    assert "is_active" in found  # константа модуля
    assert len(found) >= 8


@_needs_client_list
def test_every_backend_prefix_is_in_the_client_list() -> None:
    missing = {k: v for k, v in _fields_found().items() if k not in _client_list()}
    assert not missing, (
        "в app/** есть литерал с префиксом поля, а в FIELD_ERROR_PREFIXES записи нет "
        f"(литерал мог не уходить в detail - сверка видит только его наличие): {missing}"
    )


@_needs_client_list
def test_every_client_entry_has_a_backend_source() -> None:
    orphans = _client_list() - _fields_found().keys()
    assert not orphans, (
        f"запись FIELD_ERROR_PREFIXES без литерала с таким префиксом в app/**: {sorted(orphans)}"
    )


def test_the_not_a_prefix_list_is_not_stale() -> None:
    stale = set(_NOT_FIELD_PREFIXES) - _backend_prefixes().keys()
    assert not stale, f"_NOT_FIELD_PREFIXES называет строку, которой в app/** нет: {sorted(stale)}"


def test_the_discovery_rules_on_a_synthetic_tree(tmp_path: Path) -> None:
    # `_backend_prefixes(root)` читает любое дерево: правила проверяются на нём, потому что в
    # `app/**` докстрингов вида `слово: ` нет и пустое множество дало бы зелёный тест.
    package = tmp_path / "app"
    package.mkdir()
    (package / "sample.py").write_text(
        '"""docmod: докстринг модуля - не засчитан."""\n'
        "\n\n"
        "class Box:\n"
        '    """docclass: докстринг класса - не засчитан."""\n'
        "\n\n"
        "def make(n: int) -> list[str]:\n"
        '    """docfunc: докстринг функции - не засчитан."""\n'
        '    plain = "plainfield: обычный литерал"\n'
        '    middle = f"{n}midfield: нестартовый сегмент f-строки"\n'
        '    glued = "gluedfield" + ": конкатенация"\n'
        "    return [plain, middle, glued]\n",
        "utf-8",
    )
    assert set(_backend_prefixes(package)) == {"plainfield", "midfield"}
