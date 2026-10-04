"""`docs/requirements.md`'s numbered EARS criteria (`T-M1-23`) vs. the
test suite that's supposed to exercise them.

By design, the same shape as `test_endpoint_auth_coverage.py` (an
explicit allowlist of exceptions, editable in a diff, rather than a
free-form skip) and the same path-resolution trick as
`test_check_conflict_markers.py`/`test_task_checkbox_check.py`
(`docs/requirements.md` lives one level above `backend/`, which the
dev container doesn't mount - only CI's full checkout does).

Looks in BOTH backend/tests/**/*.py and frontend/src/**/*.test.* - a
criterion like "show the list of missing required fields" is the kind
that can only be meaningfully checked in the UI; a scan that only
looked at the backend would call it uncovered when it isn't. (This
module deliberately avoids spelling out that criterion's own ID here,
in backticks or otherwise - this file lives under backend/tests/ itself
and is part of what gets scanned, so a real ID written out in its own
docstring would count as "mentioned" without a single assertion behind
it, exactly the false positive the next paragraph warns about. Caught
by running this check against itself before writing _KNOWN_GAPS below -
see the commit message.)

Honest boundary (rule 8, CLAUDE.md): this confirms a criterion's ID is
MENTIONED somewhere in a test file - a substring match against the
raw file text, nothing more. It does not and cannot confirm that any
test actually exercises the behaviour the criterion describes; a test
could cite an ID in a comment next to an assertion about something
else entirely, and this check would still call it covered. Catching
"cited but not actually tested" is a review-round job, not something
this script can do mechanically - it is named explicitly so nobody
mistakes a green run here for proof of real coverage.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REQUIREMENTS_PATH = _REPO_ROOT / "docs" / "requirements.md"

pytestmark = pytest.mark.skipif(
    not _REQUIREMENTS_PATH.is_file(),
    reason=(
        "docs/requirements.md не резолвится по вычисленному пути "
        "(parents[2] от backend/tests) - та же причина, что и у "
        "test_check_conflict_markers.py/test_task_checkbox_check.py: "
        "внутри dev-контейнера backend смонтировано только поддерево "
        "backend/, а не весь репозиторий; в CI backend-tests запускает "
        "pytest напрямую на раннере с полным чекаутом, и путь резолвится "
        "верно"
    ),
)

_CRITERION_ID = re.compile(r"`(FR-[A-Z]+-\d+/\d{1,2})`")


def _numbered_criteria() -> set[str]:
    text = _REQUIREMENTS_PATH.read_text(encoding="utf-8")
    ids = _CRITERION_ID.findall(text)
    # `/\d{1,2}` в `_CRITERION_ID` намеренно не ловит трёхзначный "номер
    # критерия" вроде `FR-ADMIN-002/003` (сокращение НЕСКОЛЬКИХ ID
    # требований, см. преамбулу раздела 8 requirements.md) - реальные
    # номера критериев сегодня все не длиннее двух цифр (до 12 у
    # FR-AUTH-002), а у сокращения требований после `/` всегда три цифры
    # и больше (сами ID требований трёхзначные).
    return set(ids)


def _concatenated_test_sources() -> str:
    # Исключает САМ СЕБЯ: `_KNOWN_GAPS`'s ключи-одиночки (не сгенерированные
    # циклом по `range`, как у `f"FR-AUTH-002/{n}"`) неизбежно содержат
    # полный текст ID буквально (`"FR-AUTH-003/3"`) - без исключения
    # собственного файла из скана каждый такой ключ немедленно считался бы
    # "упомянутым" СОБСТВЕННЫМ присутствием в `_KNOWN_GAPS`, а не настоящим
    # тестом, и проверка на устаревшую запись (`stale_entries`) ложно
    # срабатывала бы на нём при каждом прогоне. Найдено прогоном, не
    # рассуждением: добавление первой же одиночной записи (`FR-AUTH-003/3`)
    # немедленно провалило чек со "запись устарела".
    this_file = Path(__file__).resolve()
    backend_tests = _REPO_ROOT / "backend" / "tests"
    frontend_src = _REPO_ROOT / "frontend" / "src"
    chunks = [
        path.read_text(encoding="utf-8")
        for path in backend_tests.rglob("*.py")
        if path.resolve() != this_file
    ]
    chunks += [
        path.read_text(encoding="utf-8")
        for path in frontend_src.rglob("*")
        if path.is_file() and ".test." in path.name
    ]
    return "\n".join(chunks)


# Критерии, чей идентификатор сегодня не встречается НИ В ОДНОМ тесте
# (backend/tests/**/*.py, frontend/src/**/*.test.*) - правка этого
# списка видна в дифе, по образцу `_PUBLIC_ENDPOINTS`
# (`test_endpoint_auth_coverage.py`). Запись без причины - необъявленный
# пробел, а не согласованное исключение: допустимая причина - либо имя
# коммита/требования этой же ветки `T-M1-23`, которое добавит ссылку на
# уже существующий или новый тест, либо имя ЗАДАЧИ, которая реализует
# критерий, если он сегодня не выполняется кодом вовсе.
_KNOWN_GAPS: dict[str, str] = {
    "FR-EMP-002/2": (
        "не выполняется кодом вовсе - применение нового маршрута "
        "согласования к заявкам требует FR-REQ/FR-APPR (подача и "
        "согласование заявок), ни один из них не реализован (M3). "
        "T-M1-10 (уже выполнена) сознательно не хранит маршрут "
        "денормализованно именно ради этого критерия и прямо называет "
        "T-M3-16/T-M3-20 задачами, которые его реализуют"
    ),
    "FR-AUTH-003/3": (
        "не выполняется кодом вовсе - журнал аудита (FR-AUDIT-001) не "
        "реализован нигде в приложении (grep по audit_log/AuditLog пуст); "
        "T-M6-02 уже называет именно этот случай (переключение режима "
        "аутентификации из режима внедрения на LDAP) в числе целей "
        "ретроактивного подключения middleware записи аудита"
    ),
}


def test_every_numbered_ears_criterion_is_mentioned_in_at_least_one_test() -> None:
    all_ids = _numbered_criteria()
    assert all_ids, "в requirements.md не нашлось ни одного пронумерованного критерия"

    sources = _concatenated_test_sources()
    uncovered = {criterion_id for criterion_id in all_ids if criterion_id not in sources}

    unexplained_gaps = uncovered - _KNOWN_GAPS.keys()
    assert not unexplained_gaps, (
        f"критерии без теста и без объявленного исключения: {sorted(unexplained_gaps)}"
    )

    stale_entries = _KNOWN_GAPS.keys() - uncovered
    assert not stale_entries, (
        f"запись(и) в _KNOWN_GAPS устарели - критерий уже упомянут тестом, "
        f"исключение больше не нужно: {sorted(stale_entries)}"
    )
