"""`scripts/check_basis_version.py` (T-M1-44).

`find_violations` — чистая функция (список изменённых путей, отображение
путь -> текст файла в итоговом состоянии PR -> список нарушений), без
обращения к git/gh/окружению. Импортируется напрямую как модуль скрипта
(не вызывается субпроцессом) — тестируется логика принятия решения, а
не поведение `gh pr diff`, см. `test_task_checkbox_check.py` за тем же
рассуждением для той же пары (чистая функция / CLI-обвязка).

Часть тестов (класс ниже с префиксом `real_`) использует НАСТОЯЩИЕ
исторические коммиты — `caa7d38`, `dfec5cc` (обязаны краснеть) и
`f90e2d3`, `e41a642`, `4e22502` (обязаны молчать), названные в тексте
задачи T-M1-44. Это не фикстуры "по мотивам": `caa7d38` — единственный
исторический случай версии-пары `1.9 < 1.10`, где лексикографическое
сравнение строк дало бы обратный ответ (ровно ловушка (б), ради которой
заведена задача), а `dfec5cc` — случай, где `tasks.md` меняется один,
но его «Основание» называет СРАЗУ ДВА устаревших номера
(`requirements.md`/`plan.md`). Синтетическая фикстура с придуманными
текстами подменила бы ровно то, что должна доказывать эта проверка:
что реальные, никем не подогнанные под тест тексты документов
распознаются верно.

Два независимых условия пропуска, по образцу
`test_task_checkbox_check.py`:

1. Модульный `pytestmark`: скрипт недоступен по вычисленному пути
   («два родителя вверх» от `backend/tests` не ведёт в корень
   репозитория внутри dev-контейнера `backend`, куда compose монтирует только
   `backend/app`, `alembic`, `tests` и `openapi.json`, — см.
   `test_check_conflict_markers.py` за тем же
   рассуждением). Пропускает ВСЕ тесты файла, включая синтетические
   (все вызывают `find_violations`, импортированную из скрипта).
2. Точечный `@_needs_real_history` — только на тестах с префиксом
   `real_`: путь к скрипту может резолвиться, а вот объекты пяти
   коммитов — нет (неглубокий чекаут, `fetch-depth` по умолчанию 1, их
   не приносит). В CI (`backend-tests`) это закрыто `fetch-depth: 0`
   (см. `.github/workflows/ci.yml`, уже включено для
   `test_task_checkbox_check.py`'s нужд той же природы)."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_basis_version.py"
_REPO_ROOT = _SCRIPT_PATH.parents[1]

if _SCRIPT_PATH.is_file():
    _spec = importlib.util.spec_from_file_location("check_basis_version", _SCRIPT_PATH)
    _check_basis_version = importlib.util.module_from_spec(_spec)
    sys.modules["check_basis_version"] = _check_basis_version
    _spec.loader.exec_module(_check_basis_version)
    find_violations = _check_basis_version.find_violations
    UnrecognizedCitationFormat = _check_basis_version.UnrecognizedCitationFormat
else:
    find_violations = None  # type: ignore[assignment]
    UnrecognizedCitationFormat = None  # type: ignore[assignment]

pytestmark = pytest.mark.skipif(
    not _SCRIPT_PATH.is_file(),
    reason=(
        "scripts/check_basis_version.py не резолвится по вычисленному пути "
        "(parents[2] от backend/tests) - внутри dev-контейнера backend это "
        "корень файловой системы контейнера, не корень репозитория: compose "
        "монтирует только backend/app, alembic, tests и openapi.json, scripts/ "
        "там есть, только если его скопировали вручную (docker cp; см. "
        "test_check_conflict_markers.py, та же причина); в CI backend-tests "
        "чекаутит весь репозиторий и путь резолвится верно"
    ),
)


def _basis_header(*, version: str, basis: str) -> str:
    return f"**Версия документа:** {version}\n**Основание:** {basis}\n**Статус:** …\n"


# --- (а) форматы цитат ---------------------------------------------------


def test_format_a_docs_prefix_with_comma_versiya_is_recognized() -> None:
    documents = {
        "docs/plan.md": _basis_header(
            version="1.13", basis="`docs/requirements.md`, версия 1.18 (…)"
        ),
        "docs/requirements.md": _basis_header(version="1.18", basis="n/a"),
    }

    violations = find_violations(["docs/plan.md"], documents)

    assert violations == []


def test_format_b_bare_name_with_v_is_recognized() -> None:
    documents = {
        "docs/plan.md": _basis_header(version="1.13", basis="`requirements.md` v1.18"),
        "docs/requirements.md": _basis_header(version="1.18", basis="n/a"),
    }

    violations = find_violations(["docs/plan.md"], documents)

    assert violations == []


def test_unrecognized_format_raises_not_silence() -> None:
    # Имя упомянуто, но без обратных кавычек и без "версия"/"v" — ни один
    # из двух известных форматов не совпадает. Это НЕ «нарушений нет».
    documents = {
        "docs/plan.md": _basis_header(
            version="1.13", basis="requirements.md примерно соответствует 1.18"
        ),
        "docs/requirements.md": _basis_header(version="1.18", basis="n/a"),
    }

    with pytest.raises(UnrecognizedCitationFormat):
        find_violations(["docs/plan.md"], documents)


def test_missing_required_citation_is_flagged_not_silent() -> None:
    # PR #167 review: plan.md ОБЯЗАН цитировать requirements.md
    # (_REQUIRED_CITATIONS), а в этой фикстуре имя вовсе не упомянуто в
    # "Основании" - удаление цитаты - такой же путь к "нет нарушений",
    # как исправление номера, если отсутствие молчит. Это другой случай,
    # чем нераспознанный формат (там цитата ЕСТЬ, но непонятна).
    documents = {
        "docs/plan.md": _basis_header(version="1.13", basis="(ничего не цитирует)"),
        "docs/requirements.md": _basis_header(version="1.18", basis="n/a"),
    }

    violations = find_violations(["docs/plan.md"], documents)

    assert len(violations) == 1
    assert violations[0].kind == "missing"
    assert violations[0].target_path == "docs/plan.md"
    assert violations[0].upstream_path == "docs/requirements.md"


def test_missing_citation_declared_in_allowlist_is_silent() -> None:
    # Зеркало предыдущего теста: та же фикстура, но пара объявлена в
    # _ALLOWED_WITHOUT_CITATION - сознательное исключение, а не дырка.
    documents = {
        "docs/plan.md": _basis_header(version="1.13", basis="(ничего не цитирует)"),
        "docs/requirements.md": _basis_header(version="1.18", basis="n/a"),
    }
    key = ("docs/plan.md", "docs/requirements.md")
    _check_basis_version._ALLOWED_WITHOUT_CITATION[key] = (
        "тест: временно объявлено исключением"
    )
    try:
        violations = find_violations(["docs/plan.md"], documents)
    finally:
        del _check_basis_version._ALLOWED_WITHOUT_CITATION[key]

    assert violations == []


# --- (б) версии как пары чисел -------------------------------------------


def test_1_9_vs_1_10_is_not_compared_lexicographically() -> None:
    # Лексикографически "1.9" > "1.10" (symbol '9' > '1') - обратный
    # результату как парам чисел (9 > 10 ложно). Ровно случай caa7d38.
    documents = {
        "docs/tasks.md": _basis_header(
            version="1.30",
            basis="`requirements.md` v1.0, `plan.md` v1.9, `ui.md` v1.0",
        ),
        "docs/plan.md": _basis_header(version="1.10", basis="n/a"),
        "docs/requirements.md": _basis_header(version="1.0", basis="n/a"),
        "docs/ui.md": _basis_header(version="1.0", basis="n/a"),
    }

    violations = find_violations(["docs/tasks.md"], documents)

    assert len(violations) == 1
    assert violations[0].kind == "stale"
    assert violations[0].upstream_path == "docs/plan.md"


# --- (в) собственный журнал изменений в "Основании" -----------------------


def test_cited_version_returns_the_first_citation_not_the_last() -> None:
    # PR #167 review, второй круг: прямой тест на разбор, не сквозной
    # через find_violations - различает стратегии "первое совпадение"
    # и "последнее совпадение" по построению (requirements.md процитирован
    # ДВАЖДЫ с разными номерами), а не по совпадению чисел в фикстуре.
    # Мутация _cited_version на finditer()[-1] вместо .search() обязана
    # уронить именно этот assert: (1, 18) != (1, 12).
    block = "`requirements.md` v1.18 (декларативная цитата); позже `requirements.md` v1.12 (журнал)"

    cited = _check_basis_version._cited_version(
        block, "docs/requirements.md", "docs/plan.md"
    )

    assert cited == (1, 18)


def test_own_changelog_inside_basis_does_not_false_positive() -> None:
    # plan.md-подобный текст: декларативная цитата в начале (1.18, равна
    # фактической), затем собственный журнал версий plan.md, упоминающий
    # requirements.md повторно со СТАРЫМИ номерами, заканчиваясь на 1.17 -
    # ОТЛИЧНЫМ от фактической 1.18 (PR #167 review, второй круг: исходная
    # фикстура заканчивалась на 1.18 тоже, и "взять последнее совпадение"
    # случайно давало тот же результат, что "взять первое" - этот тест не
    # отличал стратегии, несмотря на название. Теперь отличает: "последнее
    # совпадение" вернёт 1.17 != 1.18 и даст лишний "stale").
    basis = (
        "`docs/requirements.md`, версия 1.18 (...); в v1.12 ... "
        "(`requirements.md` v1.12, §13); в v1.13 решением владельца "
        "продукта (`requirements.md` v1.17, FR-AUTH-001/3) ..."
    )
    documents = {
        "docs/plan.md": _basis_header(version="1.14", basis=basis),
        "docs/requirements.md": _basis_header(version="1.18", basis="n/a"),
    }

    violations = find_violations(["docs/plan.md"], documents)

    assert violations == []


def test_declined_case_mention_is_not_parsed_as_a_citation() -> None:
    # PR #167 review, второй круг: "версии 1.12" (родительный падеж) - та
    # самая форма, которой журнал ссылается на прошлое ("называла
    # requirements.md версии 1.17") - не совпадает с _citation_pattern
    # вовсе (он принимает только "версия X.Y" именительный и "vX.Y").
    # Это сознательная узость, а не недоделка (см. docstring модуля,
    # ловушка (в)): без неё устаревший номер внутри склонённой фразы
    # читался бы как настоящая цитата и сравнивался с фактической версией
    # - "версии" упомянут (substring), поэтому результат - не "нечего
    # разобрать" (None), а UnrecognizedCitationFormat: имя есть, формат
    # не совпал, число 1.12 никогда не извлекается и не сравнивается.
    block = "называла `requirements.md` версии 1.12, фактическая стала 1.18"

    with pytest.raises(UnrecognizedCitationFormat):
        _check_basis_version._cited_version(
            block, "docs/requirements.md", "docs/plan.md"
        )


# --- файл изменён vs. не изменён ------------------------------------------


def test_changed_file_with_stale_citation_is_flagged() -> None:
    documents = {
        "docs/ui.md": _basis_header(
            version="1.4", basis="`requirements.md` v1.12, `plan.md` v1.0"
        ),
        "docs/requirements.md": _basis_header(version="1.18", basis="n/a"),
        "docs/plan.md": _basis_header(version="1.0", basis="n/a"),
    }

    violations = find_violations(["docs/ui.md"], documents)

    assert len(violations) == 1
    assert violations[0].target_path == "docs/ui.md"
    assert violations[0].upstream_path == "docs/requirements.md"
    assert violations[0].kind == "stale"


def test_unchanged_file_with_stale_citation_is_silent() -> None:
    # Тот же стейл-кейс, но docs/ui.md НЕ в changed_paths - PR его не
    # менял, падать было бы отклонённой альтернативой (см. докстринг
    # модуля и текст задачи).
    documents = {
        "docs/ui.md": _basis_header(version="1.4", basis="`requirements.md` v1.12"),
        "docs/requirements.md": _basis_header(version="1.18", basis="n/a"),
        "docs/plan.md": _basis_header(version="1.0", basis="n/a"),
    }

    violations = find_violations(["docs/requirements.md"], documents)

    assert violations == []


# --- цитата новее фактической ---------------------------------------------


def test_citation_ahead_of_actual_version_is_also_flagged() -> None:
    # Решение (см. докстринг модуля): ссылка на версию, которой ещё нет,
    # - тоже дефект прослеживаемости, с отдельным kind, не тот же "stale".
    documents = {
        "docs/plan.md": _basis_header(version="1.13", basis="`requirements.md` v1.99"),
        "docs/requirements.md": _basis_header(version="1.18", basis="n/a"),
    }

    violations = find_violations(["docs/plan.md"], documents)

    assert len(violations) == 1
    assert violations[0].kind == "ahead_of_actual"


# --- настоящие исторические коммиты ---------------------------------------


def _git_object_available(sha: str) -> bool:
    result = subprocess.run(["git", "cat-file", "-e", sha], cwd=_REPO_ROOT, capture_output=True)
    return result.returncode == 0


def _real_history_available() -> bool:
    if shutil.which("git") is None:
        return False
    return all(
        _git_object_available(sha)
        for sha in ("caa7d38", "dfec5cc", "f90e2d3", "e41a642", "4e22502")
    )


_needs_real_history = pytest.mark.skipif(
    not _real_history_available(),
    reason=(
        "нужны git и объекты пяти коммитов T-M1-44 в истории - неглубокий "
        "чекаут (fetch-depth по умолчанию 1) их не приносит; в CI "
        "backend-tests чекаутится с fetch-depth: 0 специально для этого "
        "класса тестов (см. test_task_checkbox_check.py)"
    ),
)

_ALL_DOCS = ("docs/requirements.md", "docs/plan.md", "docs/tasks.md", "docs/ui.md")


def _changed_paths_at(sha: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _documents_at(sha: str) -> dict[str, str]:
    documents: dict[str, str] = {}
    for doc in _ALL_DOCS:
        result = subprocess.run(
            ["git", "show", f"{sha}:{doc}"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            documents[doc] = result.stdout
    return documents


@_needs_real_history
def test_real_caa7d38_plan_version_pair_is_flagged() -> None:
    # caa7d38 поднимает plan.md до 1.10, но в этом же коммите ui.md/
    # tasks.md продолжают цитировать "plan.md v1.9" - ровно случай
    # ловушки (б): "1.9" < "1.10" как строки дал бы ложный "не нарушено".
    sha = "caa7d38"
    changed = _changed_paths_at(sha)
    documents = _documents_at(sha)

    assert set(changed) == {"docs/plan.md", "docs/tasks.md", "docs/ui.md"}

    violations = find_violations(changed, documents)

    stale_plan_citations = [v for v in violations if v.upstream_path == "docs/plan.md"]
    assert {v.target_path for v in stale_plan_citations} == {"docs/tasks.md", "docs/ui.md"}
    assert all(v.kind == "stale" for v in stale_plan_citations)


@_needs_real_history
def test_real_dfec5cc_tasks_double_stale_citation_is_flagged() -> None:
    sha = "dfec5cc"
    changed = _changed_paths_at(sha)
    documents = _documents_at(sha)

    assert changed == ["docs/tasks.md"]

    violations = find_violations(changed, documents)

    assert {v.upstream_path for v in violations} == {"docs/requirements.md", "docs/plan.md"}
    assert all(v.target_path == "docs/tasks.md" and v.kind == "stale" for v in violations)


@_needs_real_history
@pytest.mark.parametrize("sha", ["f90e2d3", "e41a642", "4e22502"])
def test_real_requirements_only_commits_are_silent(sha: str) -> None:
    # Эти три коммита поднимают только requirements.md, не трогая
    # plan.md/tasks.md/ui.md - requirements.md не входит в _REQUIRED_
    # CITATIONS как ЦЕЛЕВОЙ файл (он только цитируется, не цитирует),
    # поэтому проверка не должна найти ни одного нарушения.
    changed = _changed_paths_at(sha)
    documents = _documents_at(sha)

    assert changed == ["docs/requirements.md"]

    violations = find_violations(changed, documents)

    assert violations == []
