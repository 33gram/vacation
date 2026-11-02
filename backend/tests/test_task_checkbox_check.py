"""`scripts/check_task_checkbox.py` (T-M1-49).

`find_violations` — чистая функция (список заголовков коммитов, текст
`docs/tasks.md`, текст описания PR -> список нарушений), без обращения
к git/gh/окружению. Импортируется напрямую как модуль скрипта (не
вызывается субпроцессом, в отличие от `check_conflict_markers.py`) —
здесь тестируется логика принятия решения, а не поведение внешней
команды, второе тестировать нечем: сам скрипт вызывает `gh`, которого
у CI-раннера при прогоне тестов нет причин быть недоступным, но
превращать каждый тест-кейс в сетевой вызов к настоящему GitHub было бы
хрупко и медленно без всякой пользы для того, что тут проверяется.

Часть тестов (класс ниже с префиксом `real_pr_`) использует НАСТОЯЩИЕ
исторические данные — мерж-коммиты PR #143 и #144, `git log`/`git show`
на них. Это не фикстуры "по мотивам": PR #144 — единственный
исторический случай, где чек-бокс был забыт и никто не заметил, ровно
тот сценарий, который проверка обязана ловить; PR #143 содержит два
коммита (`tasks.md v1.33: T-M1-46, …`, `tasks.md v1.34: T-M1-47, …`),
упоминающих ID неотмеченных задач НЕ в начале заголовка — единственное,
что отличает правильную реализацию (якорь на начало строки) от
неправильной (поиск ID где угодно в строке): вторая реализация красила
бы PR #143 тоже, поскольку T-M1-46/T-M1-47 на тот момент истории
действительно ещё не отмечены. Синтетическая фикстура с придуманными
текстами этого различия не поймала бы — только реальные заголовки
реальных коммитов, которые кто-то писал не думая об этом тесте.

Два независимых, накладывающихся условия пропуска, а не одно:

1. Модульный `pytestmark`: скрипт `check_task_checkbox.py` недоступен по
   вычисленному пути — пропускает ВСЕ тесты файла, включая синтетические
   (все они вызывают `find_violations`, импортированную из него; без
   самого импорта не выполнится ни один). Внутри dev-контейнера
   `backend` это тот же путь, что и в `test_check_conflict_markers.py`
   («два родителя вверх» упирается в `/`, не в корень репозитория, —
   контейнер монтирует только `backend/app`, `alembic`, `tests` и `openapi.json`), и падать при
   ИМПОРТЕ модуля на уровне файла (`FileNotFoundError` внутри `exec_
   module`) — не то же самое, что штатно пропустить тест: импорт
   выполняется до срабатывания любого `skipif`, поэтому обёрнут в
   `if _SCRIPT_PATH.is_file(): ...` до объявления `pytestmark`, а не
   после.
2. Точечный `@_needs_real_history` — только на четырёх тестах ниже с
   префиксом `real_pr_`: скрипт может быть доступен (путь резолвится,
   git есть), а вот ОБЪЕКТЫ мерж-коммитов PR #143/#144 в истории — нет
   (неглубокий чекаут, `fetch-depth` по умолчанию 1, их не приносит).
   Синтетические тесты этого не требуют вовсе и продолжают
   выполняться даже тогда. В CI (`backend-tests`) это закрыто
   `fetch-depth: 0` в чекауте (см. .github/workflows/ci.yml) — там все
   девять тестов исполняются по-настоящему, ничего не пропускается."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_task_checkbox.py"
_REPO_ROOT = _SCRIPT_PATH.parents[1]

if _SCRIPT_PATH.is_file():
    _spec = importlib.util.spec_from_file_location("check_task_checkbox", _SCRIPT_PATH)
    _check_task_checkbox = importlib.util.module_from_spec(_spec)
    sys.modules["check_task_checkbox"] = _check_task_checkbox
    _spec.loader.exec_module(_check_task_checkbox)
    find_violations = _check_task_checkbox.find_violations
    find_partial_closure_notes = _check_task_checkbox.find_partial_closure_notes
else:
    find_violations = None  # type: ignore[assignment]
    find_partial_closure_notes = None  # type: ignore[assignment]

pytestmark = pytest.mark.skipif(
    not _SCRIPT_PATH.is_file(),
    reason=(
        "scripts/check_task_checkbox.py не резолвится по вычисленному "
        "пути (parents[2] от backend/tests) - внутри dev-контейнера "
        "backend это корень файловой системы контейнера, не корень "
        "репозитория: compose монтирует только backend/app, alembic, "
        "tests и openapi.json, scripts/ там есть, только если его скопировали "
        "вручную (docker cp; см. test_check_conflict_markers.py, та же "
        "причина); в CI backend-tests чекаутит весь репозиторий и путь "
        "резолвится верно"
    ),
)

_PR144_MERGE_SHA = "ccacd87cd5a5173268e5fcdc5d2fddefc627fd66"
_PR143_MERGE_SHA = "2d7a5f0dccdc0b47ef1b0f94aa42ce7bb6f831d4"


def _git_object_available(sha: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", sha],
        cwd=_REPO_ROOT,
        capture_output=True,
    )
    return result.returncode == 0


def _real_history_available() -> bool:
    if shutil.which("git") is None:
        return False
    return _git_object_available(_PR144_MERGE_SHA) and _git_object_available(_PR143_MERGE_SHA)


_needs_real_history = pytest.mark.skipif(
    not _real_history_available(),
    reason=(
        "нужны git и объекты мерж-коммитов PR #143/#144 в истории - "
        "неглубокий чекаут (fetch-depth по умолчанию 1) их не приносит, "
        "проверено прогоном (`git cat-file -t` на клоне --depth 1 отвечает "
        '"Not a valid object name"); в CI backend-tests чекаутится с '
        "fetch-depth: 0 специально для этих тестов"
    ),
)


def _commit_titles(base_sha: str, head_sha: str) -> list[str]:
    result = subprocess.run(
        ["git", "log", "--format=%s", f"{base_sha}..{head_sha}"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _tasks_md_at(sha: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{sha}:docs/tasks.md"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


# --- Настоящие PR из истории проекта -----------------------------------


@_needs_real_history
def test_real_pr144_unticked_task_is_flagged() -> None:
    titles = _commit_titles(f"{_PR144_MERGE_SHA}^1", f"{_PR144_MERGE_SHA}^2")
    tasks_md = _tasks_md_at(f"{_PR144_MERGE_SHA}^2")

    # Ровно три коммита этого PR начинаются с `T-M1-18:` (проверено
    # отдельно, `git log --format=%s` на тех же двух ссылках - см. тело
    # коммита) - фикстура и правда воспроизводит забытый чек-бокс, а не
    # что-то другое.
    assert sum(1 for t in titles if t.startswith("T-M1-18:")) == 3

    violations = find_violations(titles, tasks_md, pr_body="")

    assert [v.task_id for v in violations] == ["T-M1-18"]
    assert violations[0].kind == "not_ticked"


@_needs_real_history
def test_real_pr144_partial_closure_line_silences_the_task() -> None:
    titles = _commit_titles(f"{_PR144_MERGE_SHA}^1", f"{_PR144_MERGE_SHA}^2")
    tasks_md = _tasks_md_at(f"{_PR144_MERGE_SHA}^2")
    pr_body = "частично: T-M1-18 — LDAP-сценарий доведём отдельной веткой"

    violations = find_violations(titles, tasks_md, pr_body)

    assert violations == []


@_needs_real_history
def test_real_pr143_mid_title_mentions_are_silent() -> None:
    titles = _commit_titles(f"{_PR143_MERGE_SHA}^1", f"{_PR143_MERGE_SHA}^2")
    tasks_md = _tasks_md_at(f"{_PR143_MERGE_SHA}^2")

    # T-M1-45 отмечена ([x]) и есть коммит "T-M1-45: ..." - молчание тут
    # ожидаемо и неинтересно. Интересны T-M1-46/T-M1-47: они НЕ отмечены
    # ([ ]) на этом снимке, и их ID есть в заголовках - но в середине
    # ("tasks.md v1.33: T-M1-46, ...", "tasks.md v1.34: T-M1-47, ..."),
    # не в начале. Реализация, ищущая ID где угодно в строке, покраснела
    # бы здесь на T-M1-46 и T-M1-47 - и это была бы ошибка.
    assert any(t.startswith("T-M1-45:") for t in titles)
    assert any("T-M1-46" in t and not t.startswith("T-M1-46:") for t in titles)
    assert any("T-M1-47" in t and not t.startswith("T-M1-47:") for t in titles)

    violations = find_violations(titles, tasks_md, pr_body="")

    assert violations == []


@_needs_real_history
def test_real_unknown_task_id_gets_its_own_violation_kind() -> None:
    tasks_md = _tasks_md_at(f"{_PR144_MERGE_SHA}^2")

    violations = find_violations(
        ["T-M9-99: эта задача никогда не существовала"],
        tasks_md,
        pr_body="",
    )

    assert len(violations) == 1
    assert violations[0].task_id == "T-M9-99"
    assert violations[0].kind == "unknown_task"


# --- Синтетические случаи: не требуют git ------------------------------


_SAMPLE_TASKS_MD = (
    "- [ ] **T-M1-01** Первая задача.\n"
    "- [x] **T-M1-02** Вторая задача, уже сделана.\n"
)


def test_ticked_task_with_matching_commit_is_silent() -> None:
    violations = find_violations(
        ["T-M1-02: something"],
        _SAMPLE_TASKS_MD,
        pr_body="",
    )

    assert violations == []


def test_commit_not_starting_with_id_does_not_trigger() -> None:
    violations = find_violations(
        ["fix: see T-M1-01 for context"],
        _SAMPLE_TASKS_MD,
        pr_body="",
    )

    assert violations == []


def test_no_commits_reference_a_task_id_means_no_violations() -> None:
    violations = find_violations(
        ["chore: bump a dependency", "docs: typo"],
        _SAMPLE_TASKS_MD,
        pr_body="",
    )

    assert violations == []


def test_same_unticked_task_id_in_several_commits_is_reported_once() -> None:
    violations = find_violations(
        ["T-M1-01: part one", "T-M1-01: part two"],
        _SAMPLE_TASKS_MD,
        pr_body="",
    )

    assert [v.task_id for v in violations] == ["T-M1-01"]


def test_partial_closure_line_must_name_the_right_task_id() -> None:
    violations = find_violations(
        ["T-M1-01: something"],
        _SAMPLE_TASKS_MD,
        pr_body="частично: T-M1-02 — не тот ID",
    )

    assert [v.task_id for v in violations] == ["T-M1-01"]


def test_duplicate_task_id_uses_the_first_occurrence() -> None:
    # PR #156 review, круг 3: докстринг заявляет это как проверенное
    # прогоном, а не унаследованное из более ранней формулировки -
    # здесь тот самый прогон.
    tasks_md = (
        "- [ ] **T-M1-99** первое вхождение, не отмечено.\n"
        "- [x] **T-M1-99** второе вхождение, отмечено.\n"
    )

    violations = find_violations(["T-M1-99: something"], tasks_md, pr_body="")

    assert [v.task_id for v in violations] == ["T-M1-99"]


# --- PR #156 review, замечание 1: unknown_task vs unrecognized_checkbox_line


def test_truly_absent_task_id_is_unknown_task() -> None:
    violations = find_violations(
        ["T-M9-99: этой задачи никогда не было"],
        _SAMPLE_TASKS_MD,
        pr_body="",
    )

    assert len(violations) == 1
    assert violations[0].task_id == "T-M9-99"
    assert violations[0].kind == "unknown_task"


def test_id_present_but_not_in_a_checkbox_line_is_a_distinct_kind() -> None:
    # ID есть в тексте документа, но не в строке вида `- [ ] **ID** ...`
    # (опечатка в оформлении, а не в самом ID) - "такой задачи нет" было
    # бы неправдой (PR #156 review, замечание 1).
    tasks_md = "Где-то в тексте упоминается T-M1-01, но не как чек-бокс.\n"

    violations = find_violations(
        ["T-M1-01: something"],
        tasks_md,
        pr_body="",
    )

    assert len(violations) == 1
    assert violations[0].task_id == "T-M1-01"
    assert violations[0].kind == "unrecognized_checkbox_line"
    assert violations[0].kind != "unknown_task"


# --- PR #156 review, замечание 2: причина у "частично:" обязательна ---


def test_partial_closure_without_any_separator_or_reason_does_not_exempt() -> None:
    violations = find_violations(
        ["T-M1-01: something"],
        _SAMPLE_TASKS_MD,
        pr_body="частично: T-M1-01",
    )

    assert [v.task_id for v in violations] == ["T-M1-01"]


def test_partial_closure_with_a_bare_separator_and_no_text_does_not_exempt() -> None:
    violations = find_violations(
        ["T-M1-01: something"],
        _SAMPLE_TASKS_MD,
        pr_body="частично: T-M1-01 —",
    )

    assert [v.task_id for v in violations] == ["T-M1-01"]


def test_partial_closure_with_whitespace_only_reason_does_not_exempt() -> None:
    violations = find_violations(
        ["T-M1-01: something"],
        _SAMPLE_TASKS_MD,
        pr_body="частично: T-M1-01 —    \n",
    )

    assert [v.task_id for v in violations] == ["T-M1-01"]


def test_partial_closure_with_a_real_reason_exempts() -> None:
    violations = find_violations(
        ["T-M1-01: something"],
        _SAMPLE_TASKS_MD,
        pr_body="частично: T-M1-01 — LDAP-сценарий доведём отдельной веткой",
    )

    assert violations == []


def test_partial_closure_inside_a_blockquote_does_not_exempt() -> None:
    # PR #156 review, круг 3: цитата — это не заявление автора PR, а
    # чужой текст, который в него попал (например, процитированный
    # пример из инструкции по этой же задаче) - распознавать её как
    # настоящее закрытие было бы молчаливым снятием блокировки без
    # намерения автора. Предыдущая версия ошибочно расширяла шаблон
    # под непроверенное утверждение, что цитата тоже распознаётся -
    # здесь закреплено обратное, подтверждённое прогоном.
    violations = find_violations(
        ["T-M1-01: something"],
        _SAMPLE_TASKS_MD,
        pr_body="> частично: T-M1-01 — процитированная причина",
    )

    assert [v.task_id for v in violations] == ["T-M1-01"]


def test_partial_closure_on_a_plain_line_still_exempts() -> None:
    # Тот же текст без цитаты - должен снимать блокировку. Пара к
    # test_partial_closure_inside_a_blockquote_does_not_exempt: сужение
    # регулярного выражения не должно было задеть обычный случай.
    violations = find_violations(
        ["T-M1-01: something"],
        _SAMPLE_TASKS_MD,
        pr_body="частично: T-M1-01 — процитированная причина",
    )

    assert violations == []


# --- PR #156 review, замечание 3: find_partial_closure_notes ------------


def test_partial_closure_note_names_the_task_and_the_rule_6_precedent() -> None:
    notes = find_partial_closure_notes(
        ["T-M1-01: something"],
        _SAMPLE_TASKS_MD,
        pr_body="частично: T-M1-01 — LDAP-сценарий доведём отдельной веткой",
    )

    assert len(notes) == 1
    assert "T-M1-01" in notes[0]
    assert "T-M1-08" in notes[0]
    assert "T-M1-30" in notes[0]


def test_no_note_when_task_is_already_ticked() -> None:
    notes = find_partial_closure_notes(
        ["T-M1-02: something"],
        _SAMPLE_TASKS_MD,
        pr_body="частично: T-M1-02 — уже не имеет значения",
    )

    assert notes == []


def test_no_note_without_a_partial_closure_line() -> None:
    notes = find_partial_closure_notes(
        ["T-M1-01: something"],
        _SAMPLE_TASKS_MD,
        pr_body="",
    )

    assert notes == []
