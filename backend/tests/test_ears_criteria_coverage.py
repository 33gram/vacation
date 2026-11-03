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

Two directions (PR #187 review, Ст-1/Ст-2). (1) Every numbered criterion of the document
is cited by some test. A citation is a whole ID: the number must not be followed by another
digit, so a citation of a criterion numbered 13 does not count for the one numbered 1 (the
earlier substring match let it). (2) Every ID cited by a test exists in the document - this
catches a typo and a citation of a criterion that was renumbered or removed (the preamble of
section 8 of requirements.md warns about exactly that). The one citation that is deliberately
dangling is listed in `_KNOWN_DANGLING` with the reason, like `_KNOWN_GAPS`.

Honest boundary (rule 8, CLAUDE.md): this confirms a criterion's ID is MENTIONED somewhere in
a test file as a whole ID, and that every cited ID exists in the document - nothing more. It does
not and cannot confirm that any test actually exercises the behaviour the criterion describes; a
test could cite an ID in a comment next to an assertion about something else entirely, and this
check would still call it covered. It also does not see a citation written in a form other than
the whole ID (a loop over a range, `f"FR-AUTH-002/{n}"`, shorthand like `002/003`): such a
criterion needs an entry in `_KNOWN_GAPS` or a whole-ID citation. Catching "cited but not
actually tested" is a review-round job, not something this script can do mechanically - it is
named explicitly so nobody mistakes a green run here for proof of real coverage.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.dev_container import DEV_CONTAINER_MOUNTS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REQUIREMENTS_PATH = _REPO_ROOT / "docs" / "requirements.md"

# Пропуск - у тестов, которым нужен `docs/requirements.md`, а не у всего модуля: проверка
# `test_guard_file_names_no_criterion_literally` документ не читает и выполняется всегда.
_needs_docs = pytest.mark.skipif(
    not _REQUIREMENTS_PATH.is_file(),
    reason=(
        "docs/requirements.md не резолвится по вычисленному пути (parents[2] от "
        "backend/tests), та же причина, что у test_check_conflict_markers.py и "
        "test_task_checkbox_check.py: "
        + DEV_CONTAINER_MOUNTS
        + "; в CI backend-tests запускает pytest напрямую на раннере с полным чекаутом, "
        "и путь резолвится верно"
    ),
)

_CRITERION_ID = re.compile(r"`(FR-[A-Z]+-\d+/\d{1,2})`")
# Цитата в тестах: ID целиком. Цифры после слэша жадные, поэтому «граница после номера - не
# цифра» выполняется сама: цитата с номером 13 даёт ровно 13, а не 1.
_CITED_ID = re.compile(r"FR-[A-Z]+-\d+/\d+")


def _numbered_criteria() -> set[str]:
    text = _REQUIREMENTS_PATH.read_text(encoding="utf-8")
    ids = _CRITERION_ID.findall(text)
    # `/\d{1,2}` в `_CRITERION_ID` намеренно не ловит трёхзначное число после
    # слэша - это сокращение НЕСКОЛЬКИХ ID требований (см. преамбулу раздела 8
    # requirements.md), а не номер критерия: реальные номера критериев сегодня
    # все не длиннее двух цифр, а у сокращения требований после слэша всегда
    # три цифры и больше (сами ID требований трёхзначные).
    return set(ids)


def _concatenated_test_sources() -> str:
    # Файл сторожа НЕ исключается из скана, и исключать его не нужно: в нём нет
    # ни одного идентификатора критерия, написанного буквально (`_KNOWN_GAPS`
    # ключуется парой (требование, номер), ID собирается `_gap_id`; это
    # проверяет `test_guard_file_names_no_criterion_literally`).
    #
    # Раньше файл исключался сравнением `path.resolve() != this_file`. Этого
    # оказалось недостаточно, и причина ИЗМЕРЕНА: при втором пути к файлу
    # (в dev-контейнере рядом с bind-монтированием `/app/tests` лежала копия
    # `/backend/tests`, сделанная `docker cp`; `Path.samefile` между ними
    # вернул False - inode и устройства разные, 179922/dev 53 против
    # 64118/dev 43) `_REPO_ROOT` для файла из `/app/tests` равен `/`, скан идёт
    # по копии `/backend/tests`, `resolve()` копии не равен `this_file`, копия
    # попадает в скан со своими буквальными ID - и все пять записей объявляются
    # устаревшими, то есть пробел перестаёт быть обнаружимым. Сравнение путей
    # (и `samefile`) ломается при любом втором пути к файлу; отсутствие
    # литералов - нет.
    backend_tests = _REPO_ROOT / "backend" / "tests"
    frontend_src = _REPO_ROOT / "frontend" / "src"
    chunks = [path.read_text(encoding="utf-8") for path in backend_tests.rglob("*.py")]
    chunks += [
        path.read_text(encoding="utf-8")
        for path in frontend_src.rglob("*")
        if path.is_file() and ".test." in path.name
    ]
    return "\n".join(chunks)


def _gap_id(requirement: str, number: int) -> str:
    """ID критерия собирается здесь, в рантайме: в этом файле его нет целиком."""
    return f"{requirement}/{number}"


# Критерии, чей идентификатор сегодня не встречается НИ В ОДНОМ тесте
# (backend/tests/**/*.py, frontend/src/**/*.test.*) - правка этого
# списка видна в дифе, по образцу `_PUBLIC_ENDPOINTS`
# (`test_endpoint_auth_coverage.py`). Запись без причины - необъявленный
# пробел, а не согласованное исключение: допустимая причина - либо имя
# коммита/требования этой же ветки `T-M1-23`, которое добавит ссылку на
# уже существующий или новый тест, либо имя ЗАДАЧИ, которая реализует
# критерий, если он сегодня не выполняется кодом вовсе.
_KNOWN_GAPS: dict[tuple[str, int], str] = {
    ("FR-EMP-002", 2): (
        "не выполняется кодом вовсе - применение нового маршрута "
        "согласования к заявкам требует FR-REQ/FR-APPR (подача и "
        "согласование заявок), ни один из них не реализован (M3). "
        "T-M1-10 (уже выполнена) сознательно не хранит маршрут "
        "денормализованно именно ради этого критерия и прямо называет "
        "T-M3-16/T-M3-20 задачами, которые его реализуют"
    ),
    ("FR-AUTH-003", 3): (
        "не выполняется кодом вовсе - журнал аудита (FR-AUDIT-001) не "
        "реализован нигде в приложении (grep по audit_log/AuditLog пуст); "
        "T-M6-02 уже называет именно этот случай (переключение режима "
        "аутентификации из режима внедрения на LDAP) в числе целей "
        "ретроактивного подключения middleware записи аудита"
    ),
    ("FR-BAL-001", 3): (
        "выполняется ЧАСТИЧНО: предикат «отпуск авансом» (`is_advance_leave`, T-M2-06) "
        "есть и проверен отдельно, но критерий требует пометки ЗАЯВКИ и подтверждения "
        "HR при попытке использовать дни, а модуля заявок нет (M3) - применение "
        "предиката вынесено в T-M3-35. Номер критерия НЕ цитируется в тестах "
        "предиката, чтобы он не выглядел покрытым (тот же принцип, что у "
        "записи о восстановлении пароля ниже)"
    ),
    ("FR-BAL-004", 1): (
        "выполняется ЧАСТИЧНО: причина корректировки обязательна и проверена "
        "(POST /api/employees/{id}/balances/adjust, T-M2-11), но вторая половина "
        "критерия - запись в журнале аудита - не реализована нигде (audit_log "
        "появится в M6); T-M6-02 называет правки остатка среди целей подключения "
        "аудита. Номер не цитируется в тестах причины, чтобы критерий не выглядел "
        "покрытым целиком"
    ),
    ("FR-AUTH-002", 11): (
        "не выполняется кодом вовсе - восстановление пароля по e-mail не "
        "реализовано; test_password_reset_request_endpoint_does_not_exist "
        "(test_router.py) фиксирует именно это отсутствие, а не закрывает "
        "критерий. T-M1-30 уже существует как задача на реализацию "
        "(PR #161 review: этот критерий раньше считался покрытым только "
        "потому, что его номер был упомянут в докстринге того теста - "
        "идентификатор перенесён сюда и оттуда убран)"
    ),
}


# Цитаты в тестах, которых НЕТ в документе, и это намеренно. Правка списка видна в дифе.
_KNOWN_DANGLING: dict[tuple[str, int], str] = {
    ("FR-AUTH-002", 20): (
        "намеренный отрицательный контроль в test_business_rules_section5_guard.py: проверка "
        "того, что более длинный номер не считается упоминанием более короткого "
        "(критерия с таким номером в документе нет, и быть не должно - иначе проверка "
        "теряет смысл)"
    ),
}


@_needs_docs
def test_every_numbered_ears_criterion_is_mentioned_in_at_least_one_test() -> None:
    all_ids = _numbered_criteria()
    assert all_ids, "в requirements.md не нашлось ни одного пронумерованного критерия"

    sources = _concatenated_test_sources()
    uncovered = all_ids - set(_CITED_ID.findall(sources))

    known = {_gap_id(*key) for key in _KNOWN_GAPS}
    unexplained_gaps = uncovered - known
    assert not unexplained_gaps, (
        f"критерии без теста и без объявленного исключения: {sorted(unexplained_gaps)}"
    )

    stale_entries = known - uncovered
    assert not stale_entries, (
        f"запись(и) в _KNOWN_GAPS устарели - критерий уже упомянут тестом, "
        f"исключение больше не нужно: {sorted(stale_entries)}"
    )


@_needs_docs
def test_every_cited_criterion_exists_in_requirements() -> None:
    cited = set(_CITED_ID.findall(_concatenated_test_sources()))
    dangling = cited - _numbered_criteria()

    known = {_gap_id(*key) for key in _KNOWN_DANGLING}
    unexplained = dangling - known
    assert not unexplained, (
        f"тесты цитируют критерии, которых нет в requirements.md (опечатка или критерий "
        f"перенумерован/убран): {sorted(unexplained)}"
    )

    stale = known - dangling
    assert not stale, (
        f"запись(и) в _KNOWN_DANGLING устарели - цитаты больше нет или критерий уже есть "
        f"в документе: {sorted(stale)}"
    )


def test_guard_file_names_no_criterion_literally() -> None:
    """Файл сторожа сканируется вместе со всеми тестами, поэтому буквальный ID в нём
    засчитался бы «упомянутым» собственным присутствием. Красный здесь - причина не
    чинить сравнением путей, а убрать литерал (собрать через `_gap_id`)."""
    own_text = Path(__file__).read_text(encoding="utf-8")
    # Без обратных кавычек: сторож ищет ID подстрокой, и литерал в строке или
    # комментарии засчитывается так же, как в тексте требования.
    literals = re.findall(r"FR-[A-Z]+-\d+/\d+", own_text)
    assert not literals, f"в файле сторожа написан идентификатор критерия буквально: {literals}"
