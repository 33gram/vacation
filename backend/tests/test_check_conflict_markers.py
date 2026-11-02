"""`scripts/check_conflict_markers.py` (T-M1-51, PR #155 review).

Каждый временный репозиторий создаётся ЗА ПРЕДЕЛАМИ рабочего дерева
проекта (`tempfile.TemporaryDirectory()`, не путь внутри репозитория) —
файл с настоящими маркерами конфликта ВНУТРИ этого репозитория был бы
ровно тем, что проверка обязана ловить, и её собственный CI-прогон на
себе бы падал.

Скрипт вызывается как отдельный процесс (`subprocess.run`, не импорт
модуля и вызов функции) — он и в реальности запускается так (`make
check-conflict-markers`, шаг CI), и только так проверяется его
фактическое поведение: `cwd` процесса, реальный `git grep`, реальные
байты файла на диске (кодировка перевода строки, бинарность), а не
объекты Python, которые эти байты уже прошли через что-то, что
нормализовало разницу.

Путь к скрипту вычисляется как «два родителя вверх от этого файла»
(`backend/tests` -> `backend` -> корень репозитория, где лежит
`scripts/`) — верно на полном чекауте, каким его видит CI
(`backend-tests` запускает pytest напрямую на раннере `actions/setup-
python`, не в Docker) и каким его видит человек, запустивший pytest у
себя на машине. Внутри dev-контейнера `backend` (`python:3.12-slim`,
собран из `backend/Dockerfile`) НЕ верно ни то, ни другое: образ не
содержит `git` вовсе (`git`/подобных пакетов в базовом слое нет), а
compose монтирует в контейнер только `backend/app`, `backend/alembic`,
`backend/tests` и `backend/openapi.json`, поэтому «два родителя вверх»
от `/app/tests` ведут в корень файловой системы контейнера, где `scripts/`
нет (кроме случая, когда его скопировали вручную `docker cp`). Оба условия
проверяются, и модуль целиком пропускается, а
не падает, если хотя бы одно не выполнено — `make test-backend`
(единственный способ прогнать backend-тесты локально в этом проекте)
использует именно этот контейнер."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_conflict_markers.py"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not _SCRIPT_PATH.is_file(),
    reason=(
        "нужен git и scripts/ двумя уровнями выше backend/tests/: в образе "
        "backend (python:3.12-slim) git нет (измерено: git не найден), а "
        "scripts/ compose не монтирует - он есть в контейнере, только если его "
        "скопировали вручную (docker cp); реально прогоняется в CI, где "
        "backend-tests запускает pytest напрямую на раннере, а не в этом образе"
    ),
)


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init_repo(files: dict[str, bytes]) -> Path:
    """Создаёт временный git-репозиторий с заданными файлами (сырые
    байты — важно для CRLF/бинарных сценариев, `str` уже сгладил бы
    разницу) одним коммитом и возвращает путь к нему."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="check-conflict-markers-"))
    _run_git(tmp_dir, "init", "-q")
    _run_git(tmp_dir, "config", "user.email", "test@example.com")
    _run_git(tmp_dir, "config", "user.name", "Test")
    for relative_path, content in files.items():
        full_path = tmp_dir / relative_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)
    _run_git(tmp_dir, "add", "-A")
    _run_git(tmp_dir, "commit", "-q", "-m", "fixture")
    return tmp_dir


def _run_check(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT_PATH)],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


# --- Положительные случаи: обязаны найти маркер -----------------------


def test_default_markers_with_labels_are_detected() -> None:
    repo = _init_repo(
        {
            "conflict.txt": (
                b"<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n"
            ),
        }
    )
    result = _run_check(repo)

    assert result.returncode == 1
    assert "conflict.txt:1:" in result.stderr
    assert "conflict.txt:3:" in result.stderr
    assert "conflict.txt:5:" in result.stderr


def test_diff3_base_marker_is_detected() -> None:
    repo = _init_repo(
        {
            "conflict.txt": (
                b"<<<<<<< HEAD\nours\n||||||| base\ncommon\n=======\n"
                b"theirs\n>>>>>>> branch\n"
            ),
        }
    )
    result = _run_check(repo)

    assert result.returncode == 1
    assert "conflict.txt:3:" in result.stderr  # ||||||| base


def test_nonstandard_marker_length_via_conflict_marker_size_is_detected() -> None:
    # Воспроизводит реальный вывод git при `conflict-marker-size=9`
    # (проверено прогоном настоящего `git merge` с этим атрибутом перед
    # правкой скрипта, не придумано — см. тело коммита): маркеры
    # девятисимвольные, включая строку-разделитель `=========`.
    repo = _init_repo(
        {
            "sized.txt": (
                b"<<<<<<<<< HEAD\nours\n=========\ntheirs\n>>>>>>>>> branch\n"
            ),
        }
    )
    result = _run_check(repo)

    assert result.returncode == 1
    assert "sized.txt:1:" in result.stderr
    assert "sized.txt:5:" in result.stderr


def test_crlf_labeled_markers_are_detected() -> None:
    repo = _init_repo(
        {
            "crlf.txt": (
                b"<<<<<<< HEAD\r\nours\r\n=======\r\ntheirs\r\n>>>>>>> branch\r\n"
            ),
        }
    )
    result = _run_check(repo)

    assert result.returncode == 1
    # Строка-разделитель — именно та, что раньше пропускалась под CRLF
    # (см. тело коммита): без неё эта регрессия тихо вернулась бы.
    assert "crlf.txt:3:" in result.stderr


def test_crlf_bare_markers_are_detected() -> None:
    repo = _init_repo(
        {
            "crlf_bare.txt": (
                b"<<<<<<<\r\nours\r\n=======\r\ntheirs\r\n>>>>>>>\r\n"
            ),
        }
    )
    result = _run_check(repo)

    assert result.returncode == 1
    assert "crlf_bare.txt:1:" in result.stderr
    assert "crlf_bare.txt:3:" in result.stderr
    assert "crlf_bare.txt:5:" in result.stderr


def test_conflict_at_repo_root_is_found_when_run_from_a_subdirectory() -> None:
    repo = _init_repo(
        {
            "conflict.txt": (
                b"<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n"
            ),
            "backend/.keep": b"",
        }
    )
    result = _run_check(repo / "backend")

    assert result.returncode == 1
    assert "conflict.txt:1:" in result.stderr


# --- Отрицательные случаи: НЕ обязаны найти ничего --------------------


def test_clean_tree_passes() -> None:
    repo = _init_repo({"readme.md": b"# Title\n\nSome text.\n"})
    result = _run_check(repo)

    assert result.returncode == 0


def test_five_equals_signs_is_not_a_marker() -> None:
    repo = _init_repo({"file.md": b"text\n=====\nmore text\n"})
    result = _run_check(repo)

    assert result.returncode == 0


def test_ten_equals_signs_is_not_a_marker() -> None:
    repo = _init_repo({"file.md": b"text\n==========\nmore text\n"})
    result = _run_check(repo)

    assert result.returncode == 0


def test_seven_equals_signs_not_at_line_start_is_not_a_marker() -> None:
    repo = _init_repo({"file.md": b"prefix =======\n"})
    result = _run_check(repo)

    assert result.returncode == 0


def test_dash_rule_is_not_a_marker() -> None:
    repo = _init_repo({"file.md": b"Title\n-------\n"})
    result = _run_check(repo)

    assert result.returncode == 0


def test_binary_file_with_marker_bytes_is_ignored() -> None:
    """Подтверждает докстринг скрипта прогоном, а не рассуждением
    (правило 8, PR #155 review): без `-I` `git grep` находит этот файл
    (`Binary file ... matches`, код возврата 0 у самого `git grep`) — с
    `-I` не находит вовсе."""
    content = b"preamble\n<<<<<<< HEAD\nfoo\x00bar\n=======\nbaz\n>>>>>>> branch\n"
    repo = _init_repo({"binary.bin": content})

    without_i = subprocess.run(
        ["git", "grep", "-n", "-E", "-e", r"^<{7,}( |$)", "--", ":/"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert without_i.returncode == 0
    assert "Binary file" in without_i.stdout

    result = _run_check(repo)
    assert result.returncode == 0


@pytest.mark.parametrize("marker_char", ["<", ">", "|"])
def test_eight_repeated_marker_characters_is_now_a_real_marker(marker_char: str) -> None:
    # PR #155 review: раньше `{7}` (ровно семь) отклонял восьмисимвольный
    # ряд как случайное совпадение; при `conflict-marker-size >= 8` это
    # настоящий маркер, и он обязан ловиться.
    repo = _init_repo({"file.txt": f"{marker_char * 8}\n".encode()})
    result = _run_check(repo)

    assert result.returncode == 1
    assert "file.txt:1:" in result.stderr
