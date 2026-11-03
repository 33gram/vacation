"""Every backend-project `pip install` in a file this module's filename-
pattern list recognizes as build/CI/ops configuration (`Dockerfile*`,
`docker-compose*.yml`, `.github/workflows/*.yml`/`*.yaml`, `Makefile*`,
`*.sh`) follows the two-step hashed form (`T-M1-55`): dependencies installed from
`requirements.lock` with `--require-hashes`, the project itself installed
separately with `--no-deps`. A new install site added later in either
step's old one-shot form (`pip install -e . -c constraints.txt`, or
simply forgetting `--no-deps`/the hashed step) would install unverified
artifacts silently, and the whole point of hash-pinning disappears
without anyone noticing. Same failure class as `T-M1-46` and
`department_service`'s `head_employee_id` invariant: a rule enforced in
several places with nothing checking all of them together.

The invariant is NOT only about the project-install step, since PR #171
review: any `pip install -r requirements.lock` is checked for
`--require-hashes` on its OWN, independently of whether a project
install sits anywhere near it. A dependencies-only job (a separate CI
job, a separate workflow file, lint-only tooling) that installs the lock
file without `--require-hashes` is a real silent gap - verified by
running before this fix: a one-line workflow doing exactly that passed
with zero violations, because the main loop only ever looked at commands
that also matched "this installs the project itself".

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

Discovery by an EXPANDED filename-pattern list, not by content - `T-M1-
55`'s first edition inherited the module this replaces' pattern list
(`.github/workflows/*.yml`/`*.yaml`, `Dockerfile*`, `docker-compose*.yml`)
and that was already narrower than this docstring's own opening claim AT
THE TIME ("anywhere in the repository" - since corrected above), caught
by review (PR #171): the root
`Makefile` carries a real `pip install` today (the `pip-tools` install
inside `make lock-backend`) and matched none of those patterns, so a
`pip install -e .` added there as a new recipe went undetected - verified
by running before this fix.

Content-based discovery (read every file `git ls-files` tracks, check
its text for `pip install`) was tried first and REJECTED, verified by
running, not on suspicion: it finds every actual install site, but it
also finds every PROSE mention of the phrase "pip install" - this
module's own docstring, `docs/tasks.md`'s account of this very task,
`requirements.lock`'s header explaining the two-step form in a comment -
none of which are commands. Run against the real tree, content-based
discovery turned the real-file test's `assert not violations` into two
dozen failures from documentation text, not install sites; closing that
would need a per-file or per-line exceptions list large enough to defeat
the "discovery, not enumeration" point of scanning at all. `Dockerfile*`/
`docker-compose*.yml`/CI workflow files do not have this problem in
practice - they are config, not prose, and a `pip install` mention
inside one is either a real command or a comment about one line away
from it (the documented, accepted false positive family below) -
whereas scanning EVERY tracked file reaches markdown, docstrings and
changelog-style entries where such mentions are the norm.

`_discover_candidate_files` therefore still matches by filename, but the
pattern list is now aligned with what review actually measured as
missing, not just the three file kinds the module this replaces happened
to need: `Dockerfile*`, `docker-compose*.yml`, `.github/workflows/
*.yml`/`*.yaml` (unchanged), plus `Makefile*` (closes the measured gap)
and `*.sh` anywhere in the tree (the next most plausible kind of file to
carry a `pip install` - an entrypoint or setup script - even though none
exists in this repository today). `.git`/`node_modules` are excluded by
path part, as before. `test_discover_matches_makefile_by_pattern` below
is the regression test for the specific gap review measured.

Known, named boundary (rule 8, CLAUDE.md) - not just "not literally every
file", but which plausible install sites specifically sit outside this
pattern list today: a composite GitHub Action's own definition
(`.github/actions/*/action.yml`), `tox.ini`, and `noxfile.py` would each
be a legitimate place to write a `pip install`, and none of the five
patterns above matches any of them - confirmed by running `find` over
this repository: none of the three exists here today, so the gap is
theoretical, not measured debt the way the `Makefile`/`*.sh` gap was.
Left unclosed on purpose, not by oversight: adding a pattern for a file
kind that does not exist yet would have no regression test backing it
(the project's own convention here is a real, injected negative control
per pattern, not a speculative one) - the pattern list grows the next
time one of these three actually appears, the same way `Makefile*`/
`*.sh` were added once `make lock-backend`'s real recipe exposed the
previous list's gap.

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

A fifth case, found in this branch's own PR #171 review round, was NOT
left as a loud false positive: a comment narrating the real command as
`` `pip install -r requirements.lock --require-hashes` `` in
`backend/Dockerfile`'s prose (the flag glued to the closing Markdown
backtick, no separating whitespace) tokenized its last word as the flag
text plus a trailing backtick character, which does not equal the bare
flag `_REQUIRE_HASHES_FLAG` checks for - flagged as "missing
--require-hashes" in a line that is not a command at all. The first fix
(same PR #171 review round) removed the backticks from that one phrase
in the comment - narrow: the next prose mentioning a flag in backticks
anywhere in a scanned file reproduces the same false positive, and the
fix leaves no trace of WHY backticks were avoided there for the next
person editing that comment. Replaced by a second PR #171 review round
with a mechanism fix: `_tokenize` (below) treats a backtick the same as
whitespace before splitting a command into tokens - not Markdown
parsing, one character substitution at the same level as the command
separators above. This closes the whole class, not one phrase, and the
backtick is restored to `backend/Dockerfile`'s prose for readability.
`test_backtick_quoted_flag_in_prose_is_not_flagged` below is the
regression test for this; `test_bare_dot_install_without_no_deps_is_
flagged` and the rest of the suite above confirm a real command missing
a real flag is still caught after this change.

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

from tests.dev_container import DEV_CONTAINER_MOUNTS

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
        ".github/workflows/ не резолвится по вычисленному пути (parents[2] от "
        "backend/tests), та же причина, что у test_ears_criteria_coverage.py: "
        + DEV_CONTAINER_MOUNTS
        + "; в CI backend-tests запускает pytest напрямую на раннере с полным чекаутом, и "
        "путь резолвится верно"
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
# Необязательный префикс "./" и спуск в подкаталог ("backend/
# requirements.lock" - тот же файл, что "requirements.lock" внутри
# backend/, если смотреть из корня репозитория, где может запускаться
# workflow) называют один и тот же файл. Подъём на уровень выше
# ("../requirements.lock") сюда не попадает намеренно - это другой файл,
# не то же самое исключение (`^(?!\.\.)` отсекает его явно).
_LOCK_VALUE = re.compile(r"^(?!\.\.)(?:\./)?(?:[^./][^/]*/)*requirements\.lock\b")
_REQUIRE_HASHES_FLAG = "--require-hashes"
_NO_DEPS_FLAG = "--no-deps"

_ALLOWED_WITHOUT_HASHED_LOCK: dict[str, str] = {}


def _split_shell_commands(line: str) -> list[str]:
    # Не разбор shell: `&&`/`;`/`||` внутри кавычек не учитываются. Ни в
    # одном реальном `pip install` в этом репозитории такого нет.
    return [part for part in _COMMAND_SEPARATOR.split(line) if part.strip()]


def _tokenize(command: str) -> list[str]:
    # Бэктик - тот же разделитель, что и пробел, а не часть токена:
    # закрывающая кавычка Markdown, приклеенная к флагу в прозе
    # комментария (`` `--require-hashes` ``), иначе становится частью
    # последнего токена и не совпадает ни с одной проверяемой константой
    # (PR #171 review - воспроизведено на реальном комментарии
    # `backend/Dockerfile`, см. докстринг выше). Одна замена символа
    # перед `split()`, не разбор Markdown.
    return command.replace("`", " ").split()


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


def _command_references_lock_file(tokens: list[str]) -> bool:
    for index, token in enumerate(tokens):
        if token in _LOCK_FLAGS and index + 1 < len(tokens):
            if _LOCK_VALUE.match(tokens[index + 1]):
                return True
    return False


def _command_is_hashed_lock_install(tokens: list[str]) -> bool:
    return _command_references_lock_file(tokens) and _REQUIRE_HASHES_FLAG in tokens


def _line_has_hashed_lock_install(line: str) -> bool:
    for command in _split_shell_commands(line):
        if "pip install" not in command:
            continue
        if _command_is_hashed_lock_install(_tokenize(command)):
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
                tokens = _tokenize(command)
                stripped = command.strip()
                if stripped in _ALLOWED_WITHOUT_HASHED_LOCK:
                    continue

                # T-M1-55 PR #171 review: эта проверка - про сам замок, не
                # про установку проекта, и выполняется НЕЗАВИСИМО от неё.
                # Прежняя форма входила в разбор команды только тогда,
                # когда она ставит сам проект, и установка requirements.lock
                # без `--require-hashes` там, где рядом нет установки
                # проекта (отдельный CI-job, отдельный workflow), проходила
                # незамеченной - зависимости вставали бы без проверки хэшей,
                # и ни одна команда в файле не была бы "целью" проверки.
                if _command_references_lock_file(tokens) and _REQUIRE_HASHES_FLAG not in tokens:
                    violations.append(
                        f"{label}: {stripped} (requirements.lock без --require-hashes)"
                    )

                if not _command_targets_backend_project(tokens):
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


_CANDIDATE_RGLOB_PATTERNS = ("Dockerfile*", "docker-compose*.yml", "Makefile*", "*.sh")


def _discover_candidate_files(repo_root: Path) -> dict[str, str]:
    files: dict[str, str] = {}

    workflows_dir = repo_root / ".github" / "workflows"
    if workflows_dir.is_dir():
        for pattern in ("*.yml", "*.yaml"):
            for path in sorted(workflows_dir.glob(pattern)):
                files[str(path.relative_to(repo_root))] = path.read_text(encoding="utf-8")

    for pattern in _CANDIDATE_RGLOB_PATTERNS:
        for path in sorted(repo_root.rglob(pattern)):
            if ".git" in path.parts or "node_modules" in path.parts:
                continue
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                # Бинарный или нечитаемый файл - "pip install" в нём не
                # встретится и не нуждается в разборе.
                continue
            files[str(path.relative_to(repo_root))] = text

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


def test_lock_install_without_require_hashes_flags_even_with_no_project_install_nearby() -> None:
    # T-M1-55 PR #171 review, воссозданный пробел: до этой правки
    # `find_unhashed_project_installs` разбирало команду только если она
    # САМА ставит проект - эта строка его не ставит, и проходила бы
    # незамеченной. Ровно сценарий ревью: отдельный workflow, одна строка,
    # без --require-hashes, без установки проекта рядом.
    violations = find_unhashed_project_installs(
        {".github/workflows/lint.yml": "- run: pip install -r backend/requirements.lock\n"}
    )

    assert len(violations) == 1
    assert "requirements.lock без --require-hashes" in violations[0]


def test_lock_install_with_require_hashes_and_no_project_install_is_not_flagged() -> None:
    # Обратный контроль: тот же изолированный шаг, но с --require-hashes -
    # ничего не ставит проект, и это не обязано флагироваться.
    violations = find_unhashed_project_installs(
        {".github/workflows/lint.yml": "- run: pip install -r requirements.lock --require-hashes\n"}
    )

    assert violations == []


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


def test_backtick_quoted_flag_in_prose_is_not_flagged() -> None:
    # PR #171 review, второй круг: воссоздаёт реальный сбой из первого
    # круга на РЕАЛЬНОЙ форме комментария - фраза в прозе, флаг приклеен
    # к закрывающему бэктику без пробела. До введения `_tokenize` этот
    # текст давал ложное "requirements.lock без --require-hashes" в
    # строке, которая не является командой вовсе.
    violations = find_unhashed_project_installs(
        {
            "Dockerfile": (
                "# narrating the real command as `pip install -r "
                "requirements.lock --require-hashes`\n"
            )
        }
    )

    assert violations == []


def test_real_missing_require_hashes_is_still_flagged_after_tokenize_fix() -> None:
    # Обратный контроль к тесту выше: `_tokenize` не глотает флаг, когда
    # он по-настоящему отсутствует - лечение бэктика не стало тихим
    # пропуском настоящего нарушения.
    violations = find_unhashed_project_installs(
        {"ci.yml": "- run: pip install -r requirements.lock\n"}
    )

    assert len(violations) == 1
    assert "requirements.lock без --require-hashes" in violations[0]


# --- _discover_candidate_files само по себе ---------------------------------


def test_discover_matches_makefile_by_pattern(tmp_path: Path) -> None:
    # T-M1-55 PR #171 review, воссозданный пробел: Makefile - реальный,
    # сегодняшний файл с `pip install` (pip-tools, lock-backend), и прежний
    # шаблонный список (Dockerfile*/docker-compose*.yml/.github/workflows/
    # *.yml) его не покрывал вовсе - воспроизведено прогоном до этой правки.
    (tmp_path / "Makefile").write_text("fake-install:\n\tpip install -e .\n", encoding="utf-8")

    files = _discover_candidate_files(tmp_path)

    assert "Makefile" in files
    assert "pip install -e ." in files["Makefile"]


def test_discover_matches_shell_scripts_anywhere(tmp_path: Path) -> None:
    nested = tmp_path / "infra"
    nested.mkdir()
    (nested / "entrypoint.sh").write_text("pip install -e .\n", encoding="utf-8")

    files = _discover_candidate_files(tmp_path)

    assert "infra/entrypoint.sh" in files


def test_discover_excludes_git_and_node_modules_directories(tmp_path: Path) -> None:
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "Makefile").write_text(
        "fake:\n\tpip install -e .\n", encoding="utf-8"
    )
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "Makefile").write_text(
        "fake:\n\tpip install -e .\n", encoding="utf-8"
    )
    (tmp_path / "Makefile").write_text("real:\n\tpip install -e .\n", encoding="utf-8")

    files = _discover_candidate_files(tmp_path)

    assert list(files) == ["Makefile"]


# --- против настоящего дерева репозитория -----------------------------------


@_skip_without_full_checkout
def test_every_project_pip_install_uses_hashed_lock_with_no_deps() -> None:
    files = _discover_candidate_files(_REPO_ROOT)
    # frontend/Dockerfile обязан попасть в скан (доказательство, что он не
    # даёт ложного срабатывания - по содержимому, не по исключению).
    assert "frontend/Dockerfile" in files
    # Makefile - реальный файл с `pip install`, которого прежний шаблонный
    # список не ловил вовсе (см. тест выше).
    assert "Makefile" in files

    violations = find_unhashed_project_installs(files)

    assert not violations, "\n".join(violations)
