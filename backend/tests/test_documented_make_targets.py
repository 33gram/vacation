"""Каждая цель `make <X>`, названная в README.md и CLAUDE.md, существует в
`Makefile` (T-M2-42).

Класс «документ называет то, чего нет» (README обещал «ожидаемый запуск» после
M0, который давно состоялся) ловится механически только в одной своей части -
в имени цели. Эта часть дешёвая: имена берутся из кода-в-тексте документа
(строчный код в обратных кавычках и блоки в тройных), а не из прозы, поэтому
английское «make sure» в тексте не считается упоминанием. Цели Makefile -
строки вида `имя:` в начале строки (в том числе `имя: export ...`), без
`:=`/`?=`.

ГРАНИЦА (правило 8): проверяется СУЩЕСТВОВАНИЕ цели, а не то, что документ о
ней говорит и что она делает; не проверяются ни флаги, ни переменные команды
(`make lock-backend LOCK_UPGRADE=...`), ни упоминания целей без слова `make`
(«цель `test-e2e`»), ни другие документы (`docs/*`, `.github/*`). Порог
(`_MIN_MENTIONS`) защищает от молчаливого нуля: если разбор перестанет находить
упоминания в README, тест краснеет, а не проходит пустым.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.dev_container import DEV_CONTAINER_MOUNTS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_README = _REPO_ROOT / "README.md"
_CLAUDE = _REPO_ROOT / "CLAUDE.md"
_MAKEFILE = _REPO_ROOT / "Makefile"

# В README на момент задачи: up, test-backend, openapi-client, migrate.
_MIN_MENTIONS = 4

_FENCED = re.compile(r"```.*?```", re.DOTALL)
_INLINE = re.compile(r"`([^`\n]+)`")
_MAKE_CALL = re.compile(r"\bmake\s+([A-Za-z][A-Za-z0-9_-]*)")
_TARGET_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*)\s*:(?![=])", re.MULTILINE)


def mentioned_make_targets(document: str) -> set[str]:
    """Имена целей из `make <имя>` внутри кода документа (не прозы)."""
    code = _FENCED.findall(document)
    prose = _FENCED.sub("", document)
    code += _INLINE.findall(prose)
    return {name for chunk in code for name in _MAKE_CALL.findall(chunk)}


def defined_make_targets(makefile: str) -> set[str]:
    return set(_TARGET_LINE.findall(makefile))


def find_missing_make_targets(document: str, makefile: str) -> set[str]:
    return mentioned_make_targets(document) - defined_make_targets(makefile)


def test_missing_target_is_reported() -> None:
    makefile = "up:\n\t@true\n\nlock-backend: export X := 1\nlock-backend:\n\t@true\n"
    assert find_missing_make_targets("Запуск: `make up`.", makefile) == set()
    assert find_missing_make_targets("`make lock-backend`", makefile) == set()
    assert find_missing_make_targets("`make migrate`", makefile) == {"migrate"}
    assert find_missing_make_targets("```bash\nmake up\nmake nothing\n```", makefile) == {
        "nothing"
    }


def test_prose_is_not_a_mention() -> None:
    assert mentioned_make_targets("please make sure that it works") == set()


def test_variable_assignment_is_not_a_target() -> None:
    makefile = "LOCK_BASE_IMAGE = python@sha256:abc\nX := 1\nup:\n"
    assert defined_make_targets(makefile) == {"up"}


_skip_without_full_checkout = pytest.mark.skipif(
    not (_README.is_file() and _MAKEFILE.is_file()),
    reason=(
        "README.md/Makefile не резолвятся по вычисленному пути (parents[2] от "
        "backend/tests), та же причина, что у test_ears_criteria_coverage.py: "
        + DEV_CONTAINER_MOUNTS
        + "; в CI backend-tests запускает pytest на раннере с полным чекаутом"
    ),
)


@_skip_without_full_checkout
def test_every_make_target_named_in_the_docs_exists() -> None:
    makefile = _MAKEFILE.read_text(encoding="utf-8")
    readme_mentions = mentioned_make_targets(_README.read_text(encoding="utf-8"))
    assert len(readme_mentions) >= _MIN_MENTIONS, (
        f"в README найдено {len(readme_mentions)} упоминаний `make <цель>` "
        f"({sorted(readme_mentions)}), ожидалось не меньше {_MIN_MENTIONS}: "
        "разбор перестал находить упоминания или они пропали из README"
    )
    for document in (_README, _CLAUDE):
        if document.is_file():
            missing = find_missing_make_targets(document.read_text(encoding="utf-8"), makefile)
            assert not missing, (
                f"{document.name} называет цели, которых нет в Makefile: {sorted(missing)}"
            )
