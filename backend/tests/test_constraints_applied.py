"""Every backend-project `pip install` anywhere in the repository carries
`-c constraints.txt` (`T-M1-43`, `PR #162 review`) - a new install site
added later without `-c constraints.txt` would install by range,
silently, and the whole point of pinning disappears without anyone
noticing. Same failure class as `T-M1-46` and `department_service`'s
`head_employee_id` invariant: a rule enforced in several places with
nothing checking all of them together.

PR #162 review, round 1 checked exactly two files named by constant path
(`.github/workflows/ci.yml`, `backend/Dockerfile`). Round 2: that is a
rule enforced by DISCOVERY, not by an enumerated list, because a new
install site is likely to arrive in a NEW file - `plan.md` §8 plans M7
production deployment with `backend`/`worker` containers, almost
certainly another backend dependency install, plausibly in a file this
module has never heard of. `_discover_candidate_files` walks the repo
root instead: every `.github/workflows/*.yml`/`*.yaml`, every
`Dockerfile*` at any depth, every `docker-compose*.yml` at any depth.
`frontend/Dockerfile` is excluded by its CONTENT (it installs npm
packages, never mentions `pip install`), not by an entry in an
exceptions list - verified by running the real-file test below with it
included in the scan.

Same path-resolution guard as `test_ears_criteria_coverage.py`, for the
same reason - not inherited as a blanket property of every check in this
suite: `.github/workflows/` lives above `backend/`, which the dev
container does not mount - only CI's full checkout does. The one test
here that walks the repo tree is gated on that, as a decorator on that
single test, not a module-wide `pytestmark`: the pure-function unit
tests below touch no filesystem at all and run everywhere, including
locally, regardless of checkout depth. Do not treat
`test_dependency_pins.py`'s "no skipif needed" as a precedent for every
new check in this suite - that module's docstring explains its
skip-free-ness is specific to files living under `backend/`; the one
gated test here is not one of those.

Pure-function core, in the same spirit as `find_violations`
(`test_dependency_pins.py`): `find_unconstrained_project_installs(files)`
takes a `{label: text}` mapping (the label is the file's path relative
to the repo root, so a violation message says exactly where it is, not
just "ci.yml"/"Dockerfile" as round 1 did) and works over TEXT, never a
real shell parser. Each line containing `pip install` is split into
individual commands on `&&`/`;`/`||` - round 1 missed a second `pip
install` chained after a constrained first one on the same line,
because it checked the line as a whole and the `-c constraints.txt`
belonging to the FIRST command satisfied the check for the entire line.
Each command is then checked independently for (a) an install target
that is the backend project itself and (b) `-c constraints.txt` in that
same command. `python -m pip install --upgrade pip` and the `pip-tools`
install inside `make lock-backend` are not flagged - not because of an
entry in an exceptions list, but because neither install target matches
the "this is the backend project" pattern at all; there is nothing there
to declare an exception for.

The install-target pattern matches `.`/`-e .`, quoted extras
(`".[dev]"`/`'.[dev]'`), AND - new in round 2 - any bare or quoted token
that is itself a filesystem path: starts with `.`, `/`, or `~`. That
closes the `-e /app` false negative round 1 had (`/app` is this
project's own `WORKDIR`, and not a real-world install target for
anything else in these files) without hardcoding `/app` specifically -
a reviewer measured, real, local-directory install is exactly the
pattern this check exists to catch, by construction, not by a named
path. Matching works token by token (each command split on whitespace),
not via a single regex with a lookbehind over raw text: `-c`/
`--constraint`'s OWN argument is simply the next token and is consumed
as such, never re-examined as an install target - so `-c
../constraints.txt` and `--constraint ./constraints.txt` both read as
"constrained, with some path" (right or wrong), never as "installing
from `../`" or "installing from `./`". `-c` and `--constraint` are the
same flag to pip; both spellings are accepted, as is an optional `./`
prefix on the path (`-c ./constraints.txt` is the same file as `-c
constraints.txt`) - round 2 treated either of these as an unflagged
violation, verified by running before the fix:
    pip install -e . --constraint constraints.txt  -> flagged (wrong)
    pip install -e . -c ./constraints.txt           -> flagged (wrong)
`-c ../constraints.txt` (a different directory) stays a real violation -
the acceptance is for an equivalent spelling of the SAME file, not for
"any path ending in constraints.txt".

Honest boundary (rule 8, CLAUDE.md): this is not a shell parser and does
not try to be one. Because the install target is recognized by the SHAPE
of a path, not by parsing shell syntax, there is a whole FAMILY of loud
false positives: anything that looks like a local-path argument to `pip
install` gets read as "installing the backend project", even when it is
something else entirely. Examples actually run, not an exhaustive list:
  - A line-continuation split across two physical lines (`pip install
    -e ".[dev]"` ending the line with a backslash, with `-c
    constraints.txt` on the NEXT line) is flagged, because this module
    checks one physical line/command at a time and never joins
    continuations.
  - A commented-out line (`# pip install -e .`) is flagged, because `#`
    is not special to this module - it is text, not a shell.
  - `pip install -r ./requirements.txt` is flagged, even though `-r`
    installs from a requirements FILE, not the project.
  - `pip install ./dist/pkg-1.0-py3-none-any.whl` is flagged, even
    though it installs a built wheel, not this project's source.
All four are loud failures on something harmless, not silent misses on
something real - the deliberate direction to err in, given the choice. A
real install written any of these ways would need an
`_ALLOWED_WITHOUT_CONSTRAINTS` entry; none of these forms appears
anywhere in this repository today.

One known, ACCEPTED false NEGATIVE, left open on purpose: `-c
constraints.txt-backup` reads as constrained, because
`constraints.txt-backup` starts with the literal text `constraints.txt`
and pip itself has no notion of a word boundary there - the regex used
to recognize the flag's value stops at the same place a human skimming
the line would. Narrowing the match to exclude a trailing suffix after
`.txt` would need either a stricter grammar for the whole value token or
an explicit list of what counts as "not really constraints.txt", and
nothing in this repository has ever been named `constraints.txt-backup`
- not worth the complexity for a file name nobody uses.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS_DIR = _REPO_ROOT / ".github" / "workflows"

# Не pytestmark на весь модуль: только ПОСЛЕДНИЙ тест ниже обходит дерево
# репозитория целиком и нуждается в пропуске внутри dev-контейнера (см.
# докстринг модуля). Остальные тесты - чистые юнит-тесты над
# find_unconstrained_project_installs на синтетических строках, файловой
# системы не касаются вовсе и обязаны выполняться всегда, в том числе
# локально.
_skip_without_full_checkout = pytest.mark.skipif(
    not _WORKFLOWS_DIR.is_dir(),
    reason=(
        ".github/workflows/ не резолвится по вычисленному пути "
        "(parents[2] от backend/tests) - та же причина, что и у "
        "test_ears_criteria_coverage.py: внутри dev-контейнера backend "
        "смонтировано только поддерево backend/, а не весь репозиторий; "
        "в CI backend-tests запускает pytest напрямую на раннере с полным "
        "чекаутом, и путь резолвится верно"
    ),
)

_COMMAND_SEPARATOR = re.compile(r"&&|\|\||;")

# pip treats these as the exact same flag.
_CONSTRAINTS_FLAGS = ("-c", "--constraint")

_PROJECT_PATH_TOKEN = re.compile(
    r"""
    ^(?:
        '(?:\.{1,2}|~|/)[^'\s]*'    # путь в одинарных кавычках: '.', '/app', '.[dev]', ...
      | "(?:\.{1,2}|~|/)[^"\s]*"    # путь в двойных кавычках
      | (?:\.{1,2}|~|/)\S*          # путь без кавычек: ., /app, ./sub[extra], ...
    )$
    """,
    re.VERBOSE,
)
# Необязательный префикс "./" - "-c ./constraints.txt" и "-c constraints.txt"
# называют один и тот же файл. "-c ../constraints.txt" (другой каталог)
# сюда не попадает намеренно - это не то же самое исключение.
_CONSTRAINTS_VALUE = re.compile(r"^(?:\./)?constraints\.txt\b")

_ALLOWED_WITHOUT_CONSTRAINTS: dict[str, str] = {}


def _split_shell_commands(line: str) -> list[str]:
    # Не разбор shell: `&&`/`;`/`||` внутри кавычек не учитываются. Ни в
    # одном реальном `pip install` в этом репозитории такого нет.
    return [part for part in _COMMAND_SEPARATOR.split(line) if part.strip()]


def _command_targets_backend_project(tokens: list[str]) -> bool:
    # Токенизация по пробелам, не разбор shell: `-c`/`--constraint`
    # потребляют СЛЕДУЮЩИЙ токен как значение флага и никогда не
    # рассматриваются как цель установки сами по себе - без этого
    # `--constraint ./constraints.txt` читался бы как "установка из ./".
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token in _CONSTRAINTS_FLAGS:
            skip_next = True
            continue
        if token == "-e":
            continue
        if _PROJECT_PATH_TOKEN.match(token):
            return True
    return False


def _command_has_constraints_flag(tokens: list[str]) -> bool:
    for index, token in enumerate(tokens):
        if token in _CONSTRAINTS_FLAGS and index + 1 < len(tokens):
            if _CONSTRAINTS_VALUE.match(tokens[index + 1]):
                return True
    return False


def find_unconstrained_project_installs(files: dict[str, str]) -> list[str]:
    violations: list[str] = []
    for label in sorted(files):
        text = files[label]
        for line in text.splitlines():
            if "pip install" not in line:
                continue
            for command in _split_shell_commands(line):
                if "pip install" not in command:
                    continue
                tokens = command.split()
                if not _command_targets_backend_project(tokens):
                    continue
                stripped = command.strip()
                if stripped in _ALLOWED_WITHOUT_CONSTRAINTS:
                    continue
                if _command_has_constraints_flag(tokens):
                    continue
                violations.append(f"{label}: {stripped}")
    return violations


def _discover_candidate_files(repo_root: Path) -> dict[str, str]:
    files: dict[str, str] = {}

    if _WORKFLOWS_DIR.is_dir():
        for pattern in ("*.yml", "*.yaml"):
            for path in sorted(_WORKFLOWS_DIR.glob(pattern)):
                files[str(path.relative_to(repo_root))] = path.read_text(encoding="utf-8")

    for pattern in ("Dockerfile*", "docker-compose*.yml"):
        for path in sorted(repo_root.rglob(pattern)):
            if ".git" in path.parts or "node_modules" in path.parts:
                continue
            files[str(path.relative_to(repo_root))] = path.read_text(encoding="utf-8")

    return files


# --- find_unconstrained_project_installs само по себе ----------------------


def test_upgrading_pip_itself_is_not_a_project_install() -> None:
    violations = find_unconstrained_project_installs(
        {"ci.yml": "run: python -m pip install --upgrade pip\n"}
    )

    assert violations == []


def test_bare_dot_install_without_constraints_is_flagged() -> None:
    violations = find_unconstrained_project_installs({"ci.yml": "run: pip install -e .\n"})

    assert len(violations) == 1
    assert "pip install -e ." in violations[0]


def test_quoted_dev_extra_install_without_constraints_is_flagged() -> None:
    violations = find_unconstrained_project_installs(
        {"Dockerfile": 'RUN pip install --no-cache-dir -e ".[dev]"\n'}
    )

    assert len(violations) == 1


def test_install_with_constraints_flag_is_not_flagged() -> None:
    violations = find_unconstrained_project_installs(
        {
            "ci.yml": 'run: pip install -e ".[dev]" -c constraints.txt\n',
            "Dockerfile": "RUN pip install --no-cache-dir -e . -c constraints.txt\n",
        }
    )

    assert violations == []


def test_declared_exception_suppresses_a_matching_line(monkeypatch) -> None:
    line = "run: pip install -e ."
    assert find_unconstrained_project_installs({"ci.yml": f"{line}\n"}) == [f"ci.yml: {line}"]

    monkeypatch.setitem(
        _ALLOWED_WITHOUT_CONSTRAINTS, line, "тестовое обоснование, не настоящее исключение"
    )

    assert find_unconstrained_project_installs({"ci.yml": f"{line}\n"}) == []


def test_violation_in_a_third_previously_unlisted_file_is_caught() -> None:
    # Доказывает именно то, из-за чего заведена проверка: место установки
    # обнаруживается по содержимому файла, а не по константе с его именем.
    violations = find_unconstrained_project_installs(
        {
            "ci.yml": 'run: pip install -e ".[dev]" -c constraints.txt\n',
            "backend/Dockerfile": "RUN pip install --no-cache-dir -e . -c constraints.txt\n",
            "infra/m7-deploy.Dockerfile": "RUN pip install -e .\n",
        }
    )

    assert violations == ["infra/m7-deploy.Dockerfile: RUN pip install -e ."]


def test_absolute_path_install_target_matching_workdir_is_flagged() -> None:
    # PR #162 review, раунд 2: `RUN pip install --no-cache-dir -e /app` ->
    # до правки шаблона цели давал [] - WORKDIR нашего Dockerfile это /app,
    # естественная запись, пропущенная шаблоном, искавшим только `.`.
    violations = find_unconstrained_project_installs(
        {"Dockerfile": "RUN pip install --no-cache-dir -e /app\n"}
    )

    assert len(violations) == 1
    assert "/app" in violations[0]


def test_second_chained_install_without_constraints_is_flagged() -> None:
    # PR #162 review, раунд 2: до разбиения по `&&` вся строка считалась
    # покрытой, потому что `-c constraints.txt` нашёлся хоть где-то в ней
    # - относился к ПЕРВОЙ установке, а не ко второй.
    violations = find_unconstrained_project_installs(
        {"Dockerfile": 'RUN pip install -e . -c constraints.txt && pip install -e ".[dev]"\n'}
    )

    assert len(violations) == 1
    assert '.[dev]' in violations[0]
    assert "constraints.txt" not in violations[0]


def test_constraints_flag_pointing_elsewhere_is_still_a_violation() -> None:
    # Названное в докстринге свойство: `-c` с ЧУЖИМ путём не защищает -
    # это не настоящий constraints.txt этого проекта.
    violations = find_unconstrained_project_installs(
        {"Dockerfile": "RUN pip install -e . -c ../constraints.txt\n"}
    )

    assert len(violations) == 1


def test_double_dash_constraint_spelling_is_accepted() -> None:
    # PR #162 review, раунд 3: `-c` и `--constraint` - один и тот же флаг
    # pip. До правки: ['Dockerfile: RUN pip install -e . --constraint
    # constraints.txt'] - проверено прогоном, здесь должно быть [].
    violations = find_unconstrained_project_installs(
        {"Dockerfile": "RUN pip install -e . --constraint constraints.txt\n"}
    )

    assert violations == []


def test_dot_slash_prefixed_constraints_path_is_accepted() -> None:
    # PR #162 review, раунд 3: `./constraints.txt` - тот же файл, что
    # `constraints.txt`. До правки: ['Dockerfile: RUN pip install -e . -c
    # ./constraints.txt'] - проверено прогоном, здесь должно быть [].
    violations = find_unconstrained_project_installs(
        {"Dockerfile": "RUN pip install -e . -c ./constraints.txt\n"}
    )

    assert violations == []


def test_double_dash_constraint_with_dot_slash_path_is_accepted_and_not_a_target() -> None:
    # Обе принятые формы сразу, и то, ради чего токенизация, а не regex с
    # lookbehind: `--constraint ./constraints.txt`'s `./constraints.txt`
    # - аргумент флага, а не цель установки "из ./".
    violations = find_unconstrained_project_installs(
        {"Dockerfile": "RUN pip install -e . --constraint ./constraints.txt\n"}
    )

    assert violations == []


def test_requirements_file_install_is_a_known_accepted_false_positive() -> None:
    # Пример СЕМЕЙСТВА ложных срабатываний (докстринг модуля), не
    # отдельный случай: `-r` ставит из файла требований, не из проекта,
    # но цель распознаётся по форме пути, а не по флагу перед ней.
    violations = find_unconstrained_project_installs(
        {"Dockerfile": "RUN pip install -r ./requirements.txt\n"}
    )

    assert len(violations) == 1


def test_wheel_file_install_is_a_known_accepted_false_positive() -> None:
    # Тот же случай: путь к собранному колесу выглядит как путь к
    # каталогу проекта для этого модуля.
    violations = find_unconstrained_project_installs(
        {"Dockerfile": "RUN pip install ./dist/pkg-1.0-py3-none-any.whl\n"}
    )

    assert len(violations) == 1


def test_constraints_txt_backup_is_a_known_accepted_false_negative() -> None:
    # Решено не закрывать (докстринг модуля): граница слова после `.txt`
    # совпадает перед дефисом, `constraints.txt-backup` читается как
    # закрепление. Ничего в репозитории так не называется.
    violations = find_unconstrained_project_installs(
        {"Dockerfile": "RUN pip install -e . -c constraints.txt-backup\n"}
    )

    assert violations == []


def test_line_continuation_is_a_known_accepted_false_positive() -> None:
    # Документированное ложное срабатывание (докстринг модуля): перенос
    # строки разбивает одну команду на два физических отрезка, второй
    # (с `-c constraints.txt`) проверке не виден.
    violations = find_unconstrained_project_installs(
        {"Dockerfile": 'RUN pip install -e ".[dev]" \\\n'}
    )

    assert len(violations) == 1


def test_commented_out_line_is_a_known_accepted_false_positive() -> None:
    # Документированное ложное срабатывание (докстринг модуля): `#` не
    # является для этого модуля синтаксисом комментария, это просто текст.
    violations = find_unconstrained_project_installs({"Dockerfile": "# pip install -e .\n"})

    assert len(violations) == 1


# --- против настоящего дерева репозитория -----------------------------


@_skip_without_full_checkout
def test_every_project_pip_install_uses_constraints_file() -> None:
    files = _discover_candidate_files(_REPO_ROOT)
    # frontend/Dockerfile обязан попасть в скан (доказательство, что он
    # не даёт ложного срабатывания - по содержимому, не по исключению).
    assert "frontend/Dockerfile" in files

    violations = find_unconstrained_project_installs(files)

    assert not violations, "\n".join(violations)
