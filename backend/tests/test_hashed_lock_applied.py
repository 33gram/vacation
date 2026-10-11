"""Every backend-project `pip install` anywhere in the repository follows
the two-step hashed form (`T-M1-55`): dependencies installed from
`requirements.lock` with `--require-hashes`, the project itself installed
separately with `--no-deps`. A new install site added later in either
step's old one-shot form (`pip install -e . -c constraints.txt`, or
simply forgetting `--no-deps`/the hashed step) would install unverified
artifacts silently, and the whole point of hash-pinning disappears
without anyone noticing. Same failure class as `T-M1-46` and
`department_service`'s `head_employee_id` invariant: a rule enforced in
several places with nothing checking all of them together.

This module REPLACES `test_constraints_applied.py` (`T-M1-43`, `PR #162
review`) rather than extending it - the invariant changed shape, not
just its flag spelling. The old invariant was "one command, carrying
`-c constraints.txt`". The new invariant spans TWO commands: a
dependencies-only install (`-r requirements.lock --require-hashes`) and a
project-only install (`--no-deps`, targeting `.`/`-e .`/an extra). Why
two commands are forced, not a style choice: `--require-hashes` rejects a
local-directory requirement outright, editable or not -
"Can't verify hashes for these file:// requirements because they point to
directories" (editable) / "...because there is no single file to hash"
(non-editable) - verified by running both forms. The project step must
therefore sit OUTSIDE hash-checking mode entirely, and `--no-deps` on
that step is what stops it from re-resolving (unhashed) everything the
first step already installed.

Discovery, not an enumerated list, inherited unchanged from the module
this replaces: a new install site is likely to arrive in a NEW file -
`plan.md` §8 plans M7 production deployment with `backend`/`worker`
containers, almost certainly another backend dependency install,
plausibly in a file this module has never heard of. `_discover_candidate_
files` walks the repo root: every `.github/workflows/*.yml`/`*.yaml`,
every `Dockerfile*` at any depth, every `docker-compose*.yml` at any
depth. `frontend/Dockerfile` is excluded by its CONTENT (it installs npm
packages, never mentions `pip install`), not by an entry in an exceptions
list - verified by running the real-file test below with it included in
the scan.

Same path-resolution guard as `test_ears_criteria_coverage.py`, for the
same reason - not inherited as a blanket property of every check in this
suite: `.github/workflows/` lives above `backend/`, which the dev
container does not mount - only CI's full checkout does. The one test
here that walks the repo tree is gated on that, as a decorator on that
single test, not a module-wide `pytestmark`: the pure-function unit tests
below touch no filesystem at all and run everywhere, including locally,
regardless of checkout depth.

Pure-function core, in the same spirit as `find_violations`
(`test_dependency_pins.py`): `find_unhashed_project_installs(files)`
takes a `{label: text}` mapping (the label is the file's path relative to
the repo root) and works over TEXT, never a real shell parser. Each line
containing `pip install` is split into individual commands on
`&&`/`;`/`||` (inherited from the old module - a single chained line
missed a second, unguarded `pip install` in `T-M1-43`'s own PR #162
review). Each command is checked independently for (a) whether its
install target is the backend project itself, and, only if so, (b)
whether IT carries `--no-deps`, and (c) whether a dependencies-install
command (`-r requirements.lock --require-hashes`) appears on the SAME
physical line or the IMMEDIATELY PRECEDING one. `python -m pip install
--upgrade pip` and the `pip-tools` install inside `make lock-backend` are
not flagged - not because of an entry in an exceptions list, but because
neither install target matches the "this is the backend project" pattern
at all; there is nothing there to declare an exception for.

Why "same line or immediately preceding line", not "anywhere earlier in
the file": `.github/workflows/ci.yml` carries TWO independent install
sites in two different jobs (`backend-tests`, `openapi-client-check`).
A file-level "a hashed install exists somewhere in this file" check would
let job 2's project-install line hide behind job 1's unrelated hashed
install, since both live in the same file - verified by running: making
the pairing file-wide instead of line-adjacent turns that cross-job gap
invisible. Line adjacency is the simplest check that still tells the two
jobs' installs apart in practice, because dozens of unrelated lines
(checkout, setup-python, job names) always separate one job's install
step from another's. Written any other way - the pairing split across a
blank line or an explanatory comment between the two steps - this breaks
adjacency and is FLAGGED, a known, accepted false positive (the same
direction the family below errs in), not a silent miss.

The install-target pattern, inherited unchanged from the module this
replaces, matches `.`/`-e .`, quoted extras (`".[dev]"`/`'.[dev]'`), and
any bare or quoted token that is itself a filesystem path: starts with
`.`, `/`, or `~`. Matching works token by token (each command split on
whitespace), not via a single regex with a lookbehind over raw text:
`-c`/`--constraint`/`-r`/`--requirement`'s OWN argument is simply the
next token and is consumed as such, never re-examined as an install
target - the SAME mechanism that used to protect `-c`/`--constraint`'s
argument now also protects `-r`/`--requirement`'s, because the new
dependencies-install command (`pip install -r requirements.lock
--require-hashes`) would otherwise misread `requirements.lock` itself
(it starts with no path marker, so it would NOT have matched before) -
but `-r ./requirements.lock` would have, the `./` prefix being a path
marker. Verified by running before extending the skip list: `pip install
-r ./requirements.lock --require-hashes` read as "targeting the project"
and got checked for `--no-deps`/pairing it does not need, a wrong
question asked of the wrong command. Side effect, verified by running:
this ALSO retires `test_constraints_applied.py`'s old documented false
positive on `pip install -r ./requirements.txt` (that module's docstring
named it as a known, accepted gap) - with `-r`'s argument now skipped
generically, that command no longer matches the install-target pattern at
all, and gives `[]`, not a violation. Honest about what did NOT change:
`-c`/`--constraint` are kept in the same skip list for the same general
reason (any flag that consumes a following value should never be
examined as an install target), even though neither spelling appears in
this repository's `pip install` commands any more after `T-M1-55` retired
`constraints.txt` - this is a defensive generalization, not dead code
being carried along by accident.

Honest boundary (rule 8, CLAUDE.md): this is not a shell parser and does
not try to be one. Because the install target is recognized by the SHAPE
of a path, not by parsing shell syntax, there is a whole FAMILY of loud
false positives: anything that looks like a local-path argument to `pip
install` gets read as "installing the backend project", even when it is
something else entirely. Examples actually run, not an exhaustive list:
  - A line-continuation split across two physical lines (`pip install
    -e ".[dev]"` ending the line with a backslash, with `--no-deps` on
    the NEXT line) is flagged for missing `--no-deps`, because this
    module checks one physical line/command at a time and never joins
    continuations.
  - A commented-out line (`# pip install -e .`) is flagged, because `#`
    is not special to this module - it is text, not a shell.
  - `pip install ./dist/pkg-1.0-py3-none-any.whl` is flagged, even
    though it installs a built wheel, not this project's source - no
    flag precedes the path for this module to recognize and skip.
All three are loud failures on something harmless, not silent misses on
something real - the deliberate direction to err in, given the choice. A
real install written any of these ways would need an
`_ALLOWED_WITHOUT_HASHED_LOCK` entry; none of these forms appears
anywhere in this repository today.

One known, ACCEPTED false NEGATIVE, left open on purpose, inherited in
spirit from the module this replaces: `-r requirements.lock-backup` reads
as "the real lock file", because `requirements.lock-backup` starts with
the literal text `requirements.lock` and pip itself has no notion of a
word boundary there - the regex used to recognize the flag's value stops
at the same place a human skimming the line would. Narrowing the match to
exclude a trailing suffix after `.lock` would need either a stricter
grammar for the whole value token or an explicit list of what counts as
"not really requirements.lock", and nothing in this repository has ever
been named `requirements.lock-backup` - not worth the complexity for a
file name nobody uses.
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
# find_unhashed_project_installs на синтетических строках, файловой
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

# Флаги, чьё значение - СЛЕДУЮЩИЙ токен, никогда не цель установки сама по
# себе. `-c`/`--constraint` не встречаются в реальных командах этого
# репозитория после T-M1-55 (constraints.txt упразднён) - оставлены в
# списке как общий случай "флаг потребляет значение", не частный разбор
# под то, что есть сегодня.
_VALUE_CONSUMING_FLAGS = ("-c", "--constraint", "-r", "--requirement")

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

_LOCK_FLAGS = ("-r", "--requirement")
# Необязательный префикс "./" - "-r ./requirements.lock" и "-r
# requirements.lock" называют один и тот же файл. "-r
# ../requirements.lock" (другой каталог) сюда не попадает намеренно - это
# не то же самое исключение.
_LOCK_VALUE = re.compile(r"^(?:\./)?requirements\.lock\b")
_REQUIRE_HASHES_FLAG = "--require-hashes"
_NO_DEPS_FLAG = "--no-deps"

_ALLOWED_WITHOUT_HASHED_LOCK: dict[str, str] = {}


def _split_shell_commands(line: str) -> list[str]:
    # Не разбор shell: `&&`/`;`/`||` внутри кавычек не учитываются. Ни в
    # одном реальном `pip install` в этом репозитории такого нет.
    return [part for part in _COMMAND_SEPARATOR.split(line) if part.strip()]


def _command_targets_backend_project(tokens: list[str]) -> bool:
    # Токенизация по пробелам, не разбор shell: value-consuming флаги
    # потребляют СЛЕДУЮЩИЙ токен как значение и никогда не рассматриваются
    # как цель установки сами по себе - без этого `-r ./requirements.lock`
    # читался бы как "установка из ./".
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token in _VALUE_CONSUMING_FLAGS:
            skip_next = True
            continue
        if token == "-e":
            continue
        if _PROJECT_PATH_TOKEN.match(token):
            return True
    return False


def _command_is_hashed_lock_install(tokens: list[str]) -> bool:
    has_lock_arg = False
    for index, token in enumerate(tokens):
        if token in _LOCK_FLAGS and index + 1 < len(tokens):
            if _LOCK_VALUE.match(tokens[index + 1]):
                has_lock_arg = True
    return has_lock_arg and _REQUIRE_HASHES_FLAG in tokens


def _line_has_hashed_lock_install(line: str) -> bool:
    for command in _split_shell_commands(line):
        if "pip install" not in command:
            continue
        if _command_is_hashed_lock_install(command.split()):
            return True
    return False


def find_unhashed_project_installs(files: dict[str, str]) -> list[str]:
    violations: list[str] = []
    for label in sorted(files):
        lines = files[label].splitlines()
        for index, line in enumerate(lines):
            if "pip install" not in line:
                continue
            for command in _split_shell_commands(line):
                if "pip install" not in command:
                    continue
                tokens = command.split()
                if not _command_targets_backend_project(tokens):
                    continue
                stripped = command.strip()
                if stripped in _ALLOWED_WITHOUT_HASHED_LOCK:
                    continue
                if _NO_DEPS_FLAG not in tokens:
                    violations.append(f"{label}: {stripped} (нет --no-deps)")
                    continue
                paired = _line_has_hashed_lock_install(line) or (
                    index > 0 and _line_has_hashed_lock_install(lines[index - 1])
                )
                if not paired:
                    violations.append(
                        f"{label}: {stripped} "
                        "(нет рядом установки requirements.lock с --require-hashes)"
                    )
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


# --- find_unhashed_project_installs само по себе ----------------------------


def test_upgrading_pip_itself_is_not_a_project_install() -> None:
    violations = find_unhashed_project_installs(
        {"ci.yml": "run: python -m pip install --upgrade pip\n"}
    )

    assert violations == []


def test_bare_dot_install_without_no_deps_is_flagged() -> None:
    violations = find_unhashed_project_installs({"ci.yml": "run: pip install -e .\n"})

    assert len(violations) == 1
    assert "pip install -e ." in violations[0]
    assert "--no-deps" in violations[0]


def test_quoted_dev_extra_install_without_no_deps_is_flagged() -> None:
    violations = find_unhashed_project_installs(
        {"Dockerfile": 'RUN pip install --no-cache-dir -e ".[dev]"\n'}
    )

    assert len(violations) == 1
    assert "--no-deps" in violations[0]


def test_install_paired_on_same_line_is_not_flagged() -> None:
    violations = find_unhashed_project_installs(
        {
            "Dockerfile": (
                "RUN pip install --no-cache-dir -r requirements.lock --require-hashes "
                '&& pip install --no-cache-dir --no-deps -e ".[dev]"\n'
            )
        }
    )

    assert violations == []


def test_install_paired_on_preceding_line_is_not_flagged() -> None:
    violations = find_unhashed_project_installs(
        {
            "ci.yml": (
                "run: |\n"
                "  python -m pip install --upgrade pip\n"
                "  pip install -r requirements.lock --require-hashes\n"
                '  pip install --no-deps -e ".[dev]"\n'
            )
        }
    )

    assert violations == []


def test_declared_exception_suppresses_a_matching_line(monkeypatch) -> None:
    line = "run: pip install -e ."
    assert find_unhashed_project_installs({"ci.yml": f"{line}\n"}) == [
        f"ci.yml: {line} (нет --no-deps)"
    ]

    monkeypatch.setitem(
        _ALLOWED_WITHOUT_HASHED_LOCK, line, "тестовое обоснование, не настоящее исключение"
    )

    assert find_unhashed_project_installs({"ci.yml": f"{line}\n"}) == []


def test_violation_in_a_third_previously_unlisted_file_is_caught() -> None:
    # Доказывает именно то, из-за чего заведена проверка: место установки
    # обнаруживается по содержимому файла, а не по константе с его именем.
    violations = find_unhashed_project_installs(
        {
            "ci.yml": (
                "run: |\n"
                "  pip install -r requirements.lock --require-hashes\n"
                '  pip install --no-deps -e ".[dev]"\n'
            ),
            "backend/Dockerfile": (
                "RUN pip install --no-cache-dir -r requirements.lock --require-hashes "
                "&& pip install --no-cache-dir --no-deps -e .\n"
            ),
            "infra/m7-deploy.Dockerfile": "RUN pip install -e .\n",
        }
    )

    assert len(violations) == 1
    assert "infra/m7-deploy.Dockerfile" in violations[0]


def test_absolute_path_install_target_matching_workdir_is_flagged() -> None:
    # Унаследовано из test_constraints_applied.py (PR #162 review, раунд
    # 2): WORKDIR нашего Dockerfile - /app, естественная запись цели.
    violations = find_unhashed_project_installs(
        {"Dockerfile": "RUN pip install --no-cache-dir -e /app\n"}
    )

    assert len(violations) == 1
    assert "/app" in violations[0]


def test_second_chained_install_without_no_deps_is_flagged() -> None:
    # Первая команда (зависимости) не является целью проверки сама по
    # себе - только вторая (-e ".[dev]", без --no-deps) обязана флагнуться.
    violations = find_unhashed_project_installs(
        {
            "Dockerfile": (
                "RUN pip install -r requirements.lock --require-hashes "
                '&& pip install -e ".[dev]"\n'
            )
        }
    )

    assert len(violations) == 1
    assert '.[dev]' in violations[0]
    assert "--no-deps" in violations[0]


def test_hashed_lock_pointing_elsewhere_is_still_a_violation() -> None:
    # `-r` с ЧУЖИМ путём не защищает - это не настоящий requirements.lock
    # этого проекта (тот же довод, что был у -c/constraints.txt).
    violations = find_unhashed_project_installs(
        {
            "Dockerfile": (
                "RUN pip install -r ../requirements.lock --require-hashes "
                "&& pip install --no-deps -e .\n"
            )
        }
    )

    assert len(violations) == 1
    assert "нет рядом установки requirements.lock" in violations[0]


def test_long_form_requirement_flag_is_accepted() -> None:
    # pip принимает `-r` и `--requirement` как один и тот же флаг.
    violations = find_unhashed_project_installs(
        {
            "Dockerfile": (
                "RUN pip install --requirement requirements.lock --require-hashes "
                "&& pip install --no-deps -e .\n"
            )
        }
    )

    assert violations == []


def test_dot_slash_prefixed_lock_path_is_accepted() -> None:
    violations = find_unhashed_project_installs(
        {
            "Dockerfile": (
                "RUN pip install -r ./requirements.lock --require-hashes "
                "&& pip install --no-deps -e .\n"
            )
        }
    )

    assert violations == []


def test_requirement_flag_argument_is_not_mistaken_for_a_project_target() -> None:
    # T-M1-55: новый вклад - `-r`/`--requirement` добавлены в
    # _VALUE_CONSUMING_FLAGS именно затем, чтобы "pip install -r
    # ./requirements.lock --require-hashes" само по себе не читалось как
    # "установка из ./" и не требовало --no-deps, которого у шага
    # зависимостей нет и не должно быть.
    violations = find_unhashed_project_installs(
        {"Dockerfile": "RUN pip install -r ./requirements.lock --require-hashes\n"}
    )

    assert violations == []


def test_requirements_file_install_is_not_flagged_as_a_project_install() -> None:
    # Было ИЗВЕСТНЫМ ПРИНЯТЫМ ложным срабатыванием в test_constraints_
    # applied.py (T-M1-43): `-r`/`--requirement` не входили в список
    # value-consuming флагов там, и "pip install -r ./requirements.txt"
    # читалось как установка из "./". T-M1-55 добавил -r/--requirement в
    # список по ДРУГОЙ причине (не спутать новый шаг зависимостей с целью)
    # и этим попутно устранил старый гап - проверено прогоном, тот же
    # вход, что раньше давал 1 нарушение, теперь даёт [].
    violations = find_unhashed_project_installs(
        {"Dockerfile": "RUN pip install -r ./requirements.txt\n"}
    )

    assert violations == []


def test_wheel_file_install_is_a_known_accepted_false_positive() -> None:
    # Путь к собранному колесу выглядит как путь к каталогу проекта для
    # этого модуля - никакой флаг не предшествует ему, чтобы его пропустить.
    violations = find_unhashed_project_installs(
        {"Dockerfile": "RUN pip install ./dist/pkg-1.0-py3-none-any.whl\n"}
    )

    assert len(violations) == 1


def test_lock_file_backup_name_is_a_known_accepted_false_negative() -> None:
    # Решено не закрывать (докстринг модуля): граница слова после `.lock`
    # совпадает перед дефисом, `requirements.lock-backup` читается как
    # настоящий файл замка. Ничего в репозитории так не называется.
    violations = find_unhashed_project_installs(
        {
            "Dockerfile": (
                "RUN pip install -r requirements.lock-backup --require-hashes "
                "&& pip install --no-deps -e .\n"
            )
        }
    )

    assert violations == []


def test_line_continuation_is_a_known_accepted_false_positive() -> None:
    # Документированное ложное срабатывание (докстринг модуля): перенос
    # строки разбивает одну команду на два физических отрезка, второй
    # (с --no-deps) проверке не виден.
    violations = find_unhashed_project_installs({"Dockerfile": 'RUN pip install -e ".[dev]" \\\n'})

    assert len(violations) == 1
    assert "--no-deps" in violations[0]


def test_commented_out_line_is_a_known_accepted_false_positive() -> None:
    # Документированное ложное срабатывание (докстринг модуля): `#` не
    # является для этого модуля синтаксисом комментария, это просто текст.
    violations = find_unhashed_project_installs({"Dockerfile": "# pip install -e .\n"})

    assert len(violations) == 1


def test_blank_line_between_paired_steps_breaks_adjacency_and_is_flagged() -> None:
    # Названное в докстринге направление: разрыв смежности (пустая строка
    # между шагами) - принятое ложное срабатывание, не тихий пропуск.
    violations = find_unhashed_project_installs(
        {
            "Dockerfile": (
                "RUN pip install -r requirements.lock --require-hashes\n"
                "\n"
                "RUN pip install --no-deps -e .\n"
            )
        }
    )

    assert len(violations) == 1
    assert "нет рядом установки requirements.lock" in violations[0]


# --- против настоящего дерева репозитория -----------------------------------


@_skip_without_full_checkout
def test_every_project_pip_install_uses_hashed_lock_with_no_deps() -> None:
    files = _discover_candidate_files(_REPO_ROOT)
    # frontend/Dockerfile обязан попасть в скан (доказательство, что он не
    # даёт ложного срабатывания - по содержимому, не по исключению).
    assert "frontend/Dockerfile" in files

    violations = find_unhashed_project_installs(files)

    assert not violations, "\n".join(violations)
