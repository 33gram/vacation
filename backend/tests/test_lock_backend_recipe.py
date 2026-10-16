"""`make lock-backend`'s drift-prevention mechanism (PR #171 review,
fourth round) lives in exactly one place: a shell line in the recipe
that seeds `pip-compile`'s `--output-file` target with the current
`backend/requirements.lock` before invoking it, so `pip-compile` sees
its own prior result and keeps pins stable instead of resolving "latest
compatible" for everything unpinned. This is the SAME shape this branch
removed from five other places already (a rule enforced by a single
line/comment, nothing checking that it stays there) - the cost of it
silently disappearing is not hypothetical: it already happened once in
this same PR (nine transitive versions drifted, two review rounds to
diagnose). `find_recipe_violations` is this invariant's guard.

Separate module, not added to `test_hashed_lock_applied.py`: that
module's invariant is about `pip install` commands (the two-step hashed
form). This one is about a single `pip-compile` INVOCATION's shape
inside one specific recipe - a different question, over different
text, with no shared parsing code between them (that module tokenizes
`pip install` lines; this one locates a `cp`/`--output-file` pair and a
flag set inside `pip-compile`'s own command line). Keeping them apart
avoids forcing one module to understand two unrelated grammars.

Honest boundary (rule 8, CLAUDE.md), named directly rather than implied:
`find_recipe_violations` checks the RECIPE'S TEXT, never its execution.
It confirms the seed step and the `--output-file` target name the same
path, that `--generate-hashes` is present, and that `--upgrade`/`-U` is
absent - all three by inspecting the shell command string `lock-backend`
embeds in the Makefile. It does NOT run `pip-compile` and does NOT
confirm that seeding actually prevents drift at any given moment - the
pins could still drift if, for example, the seeded file itself were
already wrong when `lock-backend` runs, or `pip-compile` changed its own
incremental-reuse behavior in some future version. Catching an ACTUAL
drift requires running `make lock-backend` and diffing pins, exactly
as this round did by hand (`--platform linux/amd64` container,
network fetch against PyPI) - not something to run on every CI
invocation of `pytest` for cost reasons, and not something a text
check can do without running the thing it is checking.

T-M1-57 widens this boundary for ONE narrow piece: `LOCK_UPGRADE`
(`make lock-backend LOCK_UPGRADE="-P pkg==X.Y.Z ..."`, the addressed-
bump mechanism) is a Make VARIABLE substituted into the recipe
textually, which means `find_recipe_violations`'s `--upgrade`/`-U` scan
over the recipe's own literal text can never see what a caller actually
passes through it at invocation time - a text check cannot validate a
runtime value. The recipe therefore validates `LOCK_UPGRADE`'s content
itself, as its own first, separate shell invocation, BEFORE the
`docker run` step.

PR #173 review (found before this round's own review - rule 8): the
FIRST edition of this validation accepted a `-P`/`name==version` pair
by SHAPE alone (`?*==?*` - something, then `==`, then something) and
let the matched token through WHOLE, quotes and shell metacharacters
included. Since `$(LOCK_UPGRADE)` is substituted textually a SECOND
time later, into the double-quoted `bash -c "..."` string that invokes
`pip-compile`, a value shaped like `name==version` but containing an
unescaped `"` could break out of that later quoting and run arbitrary
shell commands on the HOST (confirmed by running, not reasoned about:
`LOCK_UPGRADE='-P a==1" && echo INJECTED && echo "'` printed `INJECTED`
and exited 0). The validation now additionally splits each pair on the
first `==` and rejects either half if it contains a character outside
the set that real package names (letters/digits/`.`/`_`/`-`) and real
versions (letters/digits/`.`/`+`/`!`/`-`, PEP 440's epoch/local-version
separators) are actually made of - closing the gap between what the
rejection message claimed ("only -P name==version pairs") and what the
shape check alone actually enforced.

`test_lock_upgrade_validation_*` below actually EXECUTES just that
first invocation via real `sh` (substituting `$(LOCK_UPGRADE)` the same
textual way `make` does, including `make`'s own `$$` -> `$` reduction)
- genuine behavior, not a text pattern, and the one exception to the
"never its execution" boundary stated above. It still does not run
`pip-compile` or `docker run` - only the validation step that precedes
them. `test_lock_upgrade_validation_blocks_shell_metacharacters` goes
further than checking the exit code: it asserts no injected command's
own output appears as a standalone line, which is what distinguishes
"the value was rejected" from "the rejection message happened to quote
the rejected value back, and that quoting coincidentally contains text
that looks like the marker" - the latter is NOT execution and tripped
an earlier, cruder version of this same check (substring, not line
match) for three of these parametrized cases.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MAKEFILE_PATH = _REPO_ROOT / "Makefile"

_skip_without_full_checkout = pytest.mark.skipif(
    not _MAKEFILE_PATH.is_file(),
    reason=(
        "Makefile не резолвится по вычисленному пути (parents[2] от "
        "backend/tests) - та же причина, что и у test_ears_criteria_"
        "coverage.py/test_hashed_lock_applied.py: внутри dev-контейнера "
        "backend смонтировано только поддерево backend/, корневой "
        "Makefile недоступен; в CI backend-tests запускает pytest "
        "напрямую на раннере с полным чекаутом, и путь резолвится верно"
    ),
)

_SEED_PATTERN = re.compile(r"cp\s+requirements\.lock\s+([^\s;\\]+)")
_OUTPUT_FILE_PATTERN = re.compile(r"--output-file[=\s]+([^\s;\\]+)")
_UPGRADE_FLAGS = ("--upgrade", "-U")


def _extract_target_recipe(makefile_text: str, target: str) -> str | None:
    # Makefile-рецепт - строки с ведущим табом сразу после строки
    # "target:"; первая строка без такого отступа (или конец файла)
    # заканчивает рецепт. Не разбор всего Makefile, только один целевой
    # блок - этого достаточно для текста одной команды.
    lines = makefile_text.splitlines()
    recipe_lines: list[str] = []
    in_target = False
    for line in lines:
        if line.startswith(f"{target}:"):
            in_target = True
            continue
        if in_target:
            if line.startswith("\t"):
                recipe_lines.append(line)
                continue
            break
    if not recipe_lines:
        return None
    return "\n".join(recipe_lines)


def find_recipe_violations(recipe_text: str) -> list[str]:
    violations: list[str] = []

    pip_compile_index = recipe_text.find("pip-compile")
    if pip_compile_index == -1:
        return ["рецепт не содержит вызова pip-compile"]

    output_file_match = _OUTPUT_FILE_PATTERN.search(recipe_text, pip_compile_index)
    output_file = output_file_match.group(1) if output_file_match else None
    if output_file is None:
        violations.append("вызов pip-compile не задаёт --output-file")

    seed_match = _SEED_PATTERN.search(recipe_text[:pip_compile_index])
    if seed_match is None:
        violations.append(
            "рецепт не засевает цель --output-file текущим requirements.lock "
            "до вызова pip-compile - без посева pip-compile не видит "
            "предыдущих версий и резолвит их заново (измеренный дрейф, "
            "PR #171 review, третий-четвёртый круг)"
        )
    elif output_file is not None and seed_match.group(1) != output_file:
        violations.append(
            f"посев копирует requirements.lock в {seed_match.group(1)!r}, "
            f"а pip-compile пишет в {output_file!r} - разные файлы, посев "
            "не защищает реальную цель"
        )

    tokens = recipe_text.split()
    if any(token in _UPGRADE_FLAGS for token in tokens):
        violations.append(
            "рецепт несёт --upgrade/-U - осознанный подъём версий делается "
            "через -P отдельным, явным действием (T-M1-57), не мимоходом "
            "в каждом lock-backend"
        )

    if "--generate-hashes" not in recipe_text:
        violations.append("рецепт не содержит --generate-hashes")

    return violations


# --- find_recipe_violations само по себе ------------------------------------


_BODY = "/tmp/body.txt"

_GOOD_RECIPE = (
    "\tdocker run --rm --platform linux/amd64 \\\n"
    "\t\t$(LOCK_BASE_IMAGE) bash -c \" \\\n"
    "\t\t\tpip install --no-cache-dir --quiet pip-tools==7.4.1 && \\\n"
    f"\t\t\tif [ -f requirements.lock ]; then cp requirements.lock {_BODY}; fi && \\\n"
    "\t\t\tpip-compile --extra dev --strip-extras --generate-hashes --no-header --quiet \\\n"
    f"\t\t\t\t--output-file={_BODY} pyproject.toml && \\\n"
    f"\t\t\tcat requirements.lock.header.txt {_BODY} > requirements.lock \\\n"
    '\t\t"\n'
)

_SEED_LINE = f"if [ -f requirements.lock ]; then cp requirements.lock {_BODY}; fi && \\\n"


def test_good_recipe_has_no_violations() -> None:
    assert find_recipe_violations(_GOOD_RECIPE) == []


def test_missing_seed_step_is_flagged() -> None:
    broken = _GOOD_RECIPE.replace(_SEED_LINE, "")
    violations = find_recipe_violations(broken)

    assert len(violations) == 1
    assert "не засевает" in violations[0]


def test_seed_target_mismatching_output_file_is_flagged() -> None:
    mismatched = _GOOD_RECIPE.replace(
        f"cp requirements.lock {_BODY}",
        "cp requirements.lock /tmp/something-else.txt",
    )
    violations = find_recipe_violations(mismatched)

    assert len(violations) == 1
    assert "разные файлы" in violations[0]


def test_upgrade_flag_is_flagged() -> None:
    with_upgrade = _GOOD_RECIPE.replace(
        "pip-compile --extra dev --strip-extras --generate-hashes",
        "pip-compile --extra dev --strip-extras --generate-hashes --upgrade",
    )
    violations = find_recipe_violations(with_upgrade)

    assert len(violations) == 1
    assert "--upgrade/-U" in violations[0]


def test_upgrade_package_flag_is_not_mistaken_for_bare_upgrade() -> None:
    # T-M1-57's own deliberate-bump mechanism (-P/--upgrade-package) must
    # NOT trip this check - it is a different flag, not an abbreviation
    # of --upgrade, and token equality (not substring) is what tells them
    # apart.
    with_upgrade_package = _GOOD_RECIPE.replace(
        "pip-compile --extra dev --strip-extras --generate-hashes",
        "pip-compile --extra dev --strip-extras --generate-hashes --upgrade-package=amqp==5.4.1",
    )
    violations = find_recipe_violations(with_upgrade_package)

    assert violations == []


def test_missing_generate_hashes_is_flagged() -> None:
    without_hashes = _GOOD_RECIPE.replace("--generate-hashes ", "")
    violations = find_recipe_violations(without_hashes)

    assert len(violations) == 1
    assert "--generate-hashes" in violations[0]


def test_missing_output_file_is_flagged() -> None:
    without_output = _GOOD_RECIPE.replace(f"--output-file={_BODY} ", "")
    violations = find_recipe_violations(without_output)

    assert len(violations) == 1
    assert "не задаёт --output-file" in violations[0]


# --- против настоящего Makefile ----------------------------------------------


@_skip_without_full_checkout
def test_lock_backend_recipe_seeds_before_compiling() -> None:
    makefile_text = _MAKEFILE_PATH.read_text(encoding="utf-8")
    recipe = _extract_target_recipe(makefile_text, "lock-backend")
    assert recipe is not None, "цель lock-backend не найдена в Makefile"

    violations = find_recipe_violations(recipe)

    assert not violations, "\n".join(violations)


# --- T-M1-57: LOCK_UPGRADE's own runtime validation, executed for real ------
#
# find_recipe_violations above cannot see this: LOCK_UPGRADE is a Make
# variable substituted into the recipe textually, so whatever a caller
# passes at invocation time is invisible to a check that only reads the
# recipe's own literal text. The recipe therefore validates LOCK_UPGRADE
# itself, as a separate shell invocation preceding `docker run` (see the
# module docstring). The functions below extract exactly that first
# invocation and run it via real `sh` - genuine behavior, not a pattern
# match - substituting `$(LOCK_UPGRADE)` and reducing `$$` -> `$` the
# same textual way `make` itself does before handing a recipe line to
# the shell.


def _lock_backend_validation_script(makefile_text: str, lock_upgrade: str) -> str:
    lines = makefile_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("lock-backend:"):
            start = i + 1
            break
    assert start is not None, "цель lock-backend не найдена в Makefile"

    chunk: list[str] = []
    for line in lines[start:]:
        assert line.startswith("\t"), f"ожидалась строка рецепта с табом: {line!r}"
        body = line[1:]
        if not chunk and body.startswith("@"):
            body = body[1:]
        chunk.append(body)
        if not body.rstrip().endswith("\\"):
            # Первая строка рецепта БЕЗ продолжения `\` заканчивает эту
            # shell-инвокацию (make запускает каждую непродолженную
            # строку рецепта отдельной командой) - здесь кончается
            # валидация, до отдельной, не связанной с ней команды
            # `docker run`.
            break

    script = "\n".join(chunk)
    script = script.replace("$(LOCK_UPGRADE)", lock_upgrade)
    script = script.replace("$$", "$")
    return script


def _run_lock_upgrade_validation(lock_upgrade: str) -> subprocess.CompletedProcess[str]:
    makefile_text = _MAKEFILE_PATH.read_text(encoding="utf-8")
    script = _lock_backend_validation_script(makefile_text, lock_upgrade)
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True)


@_skip_without_full_checkout
@pytest.mark.parametrize(
    "lock_upgrade",
    [
        "",
        "-P amqp==5.4.1",
        "-P amqp==5.4.1 -P pydantic==2.14.1 -P pydantic-core==2.50.1",
    ],
)
def test_lock_upgrade_validation_accepts_well_formed_pins(lock_upgrade: str) -> None:
    result = _run_lock_upgrade_validation(lock_upgrade)

    assert result.returncode == 0, result.stderr


@_skip_without_full_checkout
@pytest.mark.parametrize(
    "lock_upgrade",
    [
        "--upgrade",
        "-U",
        "-P amqp==5.4.1 --upgrade",
        "-P amqp==5.4.1 -U",
        "-P",
        "amqp==5.4.1",
        "-P amqp",
        "-P amqp== -P pydantic==2.14.1",
    ],
)
def test_lock_upgrade_validation_rejects_everything_else(lock_upgrade: str) -> None:
    # Отрицательный контроль из п. 1 задачи: попытка протащить --upgrade/
    # -U (голым или подмешанным к валидным -P парам) через переменную
    # обязана закончиться отказом, а не тихим полным апгрейдом. Это тот
    # же самый сценарий, что был прогнан руками против настоящего `make
    # lock-backend LOCK_UPGRADE=...` (см. тело коммита) - здесь закреплён
    # тестом, воспроизводимо.
    result = _run_lock_upgrade_validation(lock_upgrade)

    assert result.returncode != 0, (
        f"LOCK_UPGRADE={lock_upgrade!r} должна была быть отвергнута, "
        f"прошла с кодом {result.returncode}"
    )
    assert "LOCK_UPGRADE" in result.stderr, result.stderr


# --- PR #173 review: значение проверялось формой `?*==?*`, а не допустимыми
# --- символами - любой токен с "==" где-то внутри принимался ЦЕЛИКОМ, кавычки
# --- и метасимволы shell включительно -------------------------------------
#
# `$(LOCK_UPGRADE)` подставляется в рецепт ТЕКСТУАЛЬНО (как и в этот
# валидационный шаг, и позже - в строку `bash -c "..."` вызова
# `pip-compile`), поэтому значение, вышедшее за рамки `-P имя==версия`,
# способно вырваться из охватывающих кавычек и исполниться как часть
# ОКРУЖАЮЩЕЙ shell-команды - проверено прогоном на настоящем `Makefile`
# (без заглушки для `docker`, см. тело коммита): `LOCK_UPGRADE='-P a==1"
# && echo INJECTED && echo "'` печатало `INJECTED` и отработало с кодом 0
# до этого круга. Каждый случай ниже проверяет НЕ ТОЛЬКО код возврата, а
# то, что маркер инъекции не появился ни в stdout, ни в stderr - отказ по
# коду без этой проверки не отличил бы «отклонено валидацией» от «команда
# инъекции сама завершилась с ошибкой, но успела что-то вывести».
_INJECTION_MARKER = "LOCK_UPGRADE_INJECTION_SHOULD_NOT_RUN"


@_skip_without_full_checkout
@pytest.mark.parametrize(
    "lock_upgrade",
    [
        # Кавычка - воспроизводит находку проверяющего буквально.
        f'-P a==1" && echo {_INJECTION_MARKER} && echo "',
        # && / ; / | - три разных способа дописать вторую команду.
        f"-P pkg==1 && echo {_INJECTION_MARKER}",
        f"-P pkg==1; echo {_INJECTION_MARKER}",
        f"-P pkg==1 | echo {_INJECTION_MARKER}",
        # $ - подстановка команды через $(...) и через обратные кавычки.
        f"-P pkg==$(echo {_INJECTION_MARKER})",
        f"-P pkg==`echo {_INJECTION_MARKER}`",
        # Пробел внутри кавычек - делает токен, не совпадающий по форме с
        # распознаваемым -P/значение, но принимавшийся прежним образцом
        # `?*==?*`, который пробел не отличал от любого другого символа.
        '-P "pkg==1 2"',
    ],
)
def test_lock_upgrade_validation_blocks_shell_metacharacters(lock_upgrade: str) -> None:
    # Проверяется не подстрока маркера, а ОТДЕЛЬНАЯ СТРОКА, равная ему
    # целиком: отказ сам по себе честно цитирует отвергнутое значение в
    # сообщении об ошибке (`LOCK_UPGRADE: версия '...MARKER...' содержит
    # символ...`), и маркер оказывается подстрокой этого сообщения без
    # какого-либо исполнения - проверено прогоном, что три из семи
    # случаев ложно падали именно по этой причине при подстрочном
    # сравнении. Настоящий `echo MARKER`, если бы он исполнился, напечатал
    # бы MARKER один, на своей строке, без кавычек и окружающего текста -
    # только это отличает «инъекция выполнилась» от «отказ процитировал
    # то, что отверг».
    result = _run_lock_upgrade_validation(lock_upgrade)

    assert result.returncode != 0, (
        f"LOCK_UPGRADE={lock_upgrade!r} должна была быть отвергнута, "
        f"прошла с кодом {result.returncode}"
    )
    executed_marker_lines = [
        line
        for line in result.stdout.splitlines() + result.stderr.splitlines()
        if line == _INJECTION_MARKER
    ]
    assert not executed_marker_lines, (
        f"LOCK_UPGRADE={lock_upgrade!r} - маркер инъекции напечатан своей "
        f"строкой, команда исполнилась: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
