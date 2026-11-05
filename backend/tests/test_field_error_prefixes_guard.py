"""Перечень префиксов полей на клиенте совпадает с тем, что размечает бэкенд (`T-M2-27`).

`FIELD_ERROR_PREFIXES` в `frontend/src/api/errors.ts` - закрытый список полей, ошибки которых
интерфейс кладёт под поле формы по префиксу `"<поле>: "` в тексте отказа (`CLAUDE.md`
«Соглашения»). Раньше список сверялся с бэкендом разбором дерева `raise` вручную (`T-M1-20`
review); теперь это делает тест, в обе стороны:
  - префикс в `app/**` без записи в перечне: отказ молча показывался бы общим текстом;
  - запись перечня без источника на бэкенде: интерфейс ждёт префикс, которого никто не шлёт.

Обнаружение - разбор AST (`ast`), не построчный `grep`: строка отказа может стоять на следующей
строке после `raise` или быть собрана из соседних литералов.

ГРАНИЦЫ (правило 8). Обнаружение видит ТОЛЬКО такие формы:
  1. строковый литерал в любом месте `app/**` (в том числе константа модуля, аргумент `raise`,
     `return` помощника вроде `personnel_number_conflict_message`), кроме докстрингов;
  2. начальный литерал f-строки (`f"department_id: подразделение {id} не найдено"`);
  3. соседние литералы (`"a: " "b"`) - их склеивает сам разбор Python.
Не видит: префикс, собранный во время выполнения (`f"{key}: {text}"` в `RuleValuesInvalidError` -
ключи реестра разбирает `rulesErrorsByKey` по ключам из ответа `GET`, не этот перечень),
конкатенацию `"поле" + ": текст"`, префикс не в начале строки, текст из внешнего файла.
Строка вида `слово: ` в `app/**`, которая не префикс поля (`_NOT_FIELD_PREFIXES`), называется
явно с причиной. Что смонтировано в dev-контейнер `backend` - `tests/dev_container.py`:
`frontend/src` там нет, тест пропускается, пока каталог не скопирован туда вручную, в CI
выполняется.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.dev_container import DEV_CONTAINER_MOUNTS

_APP = Path(__file__).resolve().parents[1] / "app"
_ERRORS_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "errors.ts"

pytestmark = pytest.mark.skipif(
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
    """Префикс -> где найден (файл:строка), по формам 1-3 из докстринга модуля."""
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            text = None
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) not in docstrings:
                    text = node.value
            elif (
                isinstance(node, ast.JoinedStr)
                and node.values
                and isinstance(node.values[0], ast.Constant)
            ):
                text = node.values[0].value
            match = _PREFIX.match(text) if text else None
            if match:
                where = f"{path.relative_to(root.parent)}:{node.lineno}"
                found.setdefault(match.group(1), []).append(where)
    return found


def _fields_found() -> dict[str, list[str]]:
    return {k: v for k, v in _backend_prefixes().items() if k not in _NOT_FIELD_PREFIXES}


def test_the_discovery_is_not_blind() -> None:
    # Приметы, которые нашлись бы при любом работающем обнаружении: по одной на каждую форму.
    found = _fields_found()
    assert "personnel_number" in found  # литерал в f-строке внутри помощника
    assert "manager_id" in found  # литерал в `raise ValueError(...)`
    assert "is_active" in found  # константа модуля
    assert len(found) >= 8


def test_every_backend_prefix_is_in_the_client_list() -> None:
    missing = {k: v for k, v in _fields_found().items() if k not in _client_list()}
    assert not missing, (
        "префикс размечается на бэкенде, но не внесён в FIELD_ERROR_PREFIXES "
        f"(ошибка показалась бы общим текстом): {missing}"
    )


def test_every_client_entry_has_a_backend_source() -> None:
    orphans = _client_list() - _fields_found().keys()
    assert not orphans, f"запись FIELD_ERROR_PREFIXES без источника на бэкенде: {sorted(orphans)}"


def test_the_not_a_prefix_list_is_not_stale() -> None:
    stale = set(_NOT_FIELD_PREFIXES) - _backend_prefixes().keys()
    assert not stale, f"_NOT_FIELD_PREFIXES называет строку, которой в app/** нет: {sorted(stale)}"
