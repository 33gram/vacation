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
bump mechanism) is a value a caller supplies at invocation time, and
whatever it carries is invisible to a check that only reads the
recipe's own literal text - a text check cannot validate a runtime
value. The recipe therefore validates it itself, as its own first,
separate shell invocation, BEFORE the `docker run` step.

PR #173 review, 1st round (found before this round's own review - rule
8): the FIRST edition of this validation accepted a `-P`/`name==version`
pair by SHAPE alone (`?*==?*` - something, then `==`, then something)
and let the matched token through WHOLE, quotes and shell metacharacters
included. `$(LOCK_UPGRADE)` was substituted textually a SECOND time,
into the double-quoted `bash -c "..."` string that invokes
`pip-compile`, so a value shaped like `name==version` but containing an
unescaped `"` could break out of that later quoting and run arbitrary
shell commands on the HOST (confirmed by running: `LOCK_UPGRADE='-P
a==1" && echo INJECTED && echo "'` printed `INJECTED` and exited 0).
Fixed that round by splitting each pair on the first `==` and rejecting
either half if it contains a character outside the set real package
names (letters/digits/`.`/`_`/`-`) and real versions
(letters/digits/`.`/`+`/`!`/`-`, PEP 440's epoch/local-version
separators) are actually made of.

PR #173 review, 2nd round (found before this round's own review too):
that character-set fix did not touch the actual CAUSE - `$(LOCK_UPGRADE)`
was still substituted by Make TEXTUALLY into the shell command's own
source, in BOTH places (the validation loop's `for tok in
$(LOCK_UPGRADE)` and the later `pip-compile` line), so the shell's own
PARSING of that line - which happens before any `case` pattern ever
runs - already executes backtick/`$(...)` command substitution found
inside the value. No character-set check can prevent this: it only
inspects the RESULT of a substitution it has no power to stop.
Confirmed by running: `LOCK_UPGRADE='-P a==`touch /tmp/PWNED`'` exited
2 (validation correctly rejected the resulting token) - but `/tmp/PWNED`
was created anyway, because `touch` ran during the shell's OWN word-
splitting of the (by-then Make-substituted) text, strictly before the
`case` pattern that rejected it ever got a chance to run.

Fixed by moving the value out of the recipe's CODE entirely and into
the ENVIRONMENT: `lock-backend: export LOCK_UPGRADE_VALUE := $(LOCK_UPGRADE)`
sets an exported Make variable scoped to this target, and the recipe
reads it as a plain shell variable (`$$LOCK_UPGRADE_VALUE` ->
`$LOCK_UPGRADE_VALUE` after Make's own `$$` -> `$` reduction), tokenized
via `set -- $$LOCK_UPGRADE_VALUE` (deliberately unquoted, for IFS word-
splitting) and consumed as `"$$@"`. Expanding an already-set shell
variable is a single-pass operation - it does not re-scan its own
result for command substitution, the way parsing a literal source line
does. Confirmed by running in isolation (not reasoned about):
`export LOCK_UPGRADE_VALUE='-P a==`touch /tmp/PWNED`'; sh -c 'set --
$LOCK_UPGRADE_VALUE; for t in "$@"; do echo "[$t]"; done'` prints the
backticks back as literal text and creates no file - true whether that
`sh` runs on the host or, forwarded via `docker run -e
LOCK_UPGRADE_VALUE`, inside the container. `docker run`'s `bash -c '...'`
is single-quoted now, not double - a double-quoted host-side string
would let the HOST shell expand `$LOCK_UPGRADE_VALUE` into the argument
handed to the container's `bash -c`, and that argument gets parsed as
FRESH CODE once it reaches the container, reopening the exact same hole
one quoting layer later. Single-quoting keeps the literal text
`$LOCK_UPGRADE_VALUE` intact until the container's own shell expands it
from its OWN environment - the only point where expansion happens.

`test_lock_upgrade_validation_*` below actually EXECUTES just that
first invocation via real `sh` (reducing `make`'s own `$$` -> `$` the
same textual way `make` does, and passing the test's value through a
REAL environment variable - `LOCK_UPGRADE_VALUE` - never through text
substitution into the script, since the real mechanism no longer does
that either) - genuine behavior, not a text pattern, and the one
exception to the "never its execution" boundary stated above. It still
does not run `pip-compile` or `docker run` - only the validation step
that precedes them.

`test_lock_upgrade_validation_blocks_shell_injection` checks each case
by SIDE EFFECT - attempting to create a marker file in a fresh `tmp_path`
and asserting it does not exist - not by scanning output text. That is
not a style preference: checking for a marker STRING in stdout/stderr
is exactly the check that missed the backtick-pre-execution case above,
since `touch` produces no output on success at all - a string-based
check would have reported "no injection" whether or not the file was
actually created. A still-earlier edition of this same test checked
for the marker as a *substring* of output, which gave 3 false failures
of its own (the rejection message honestly quotes back the value it
rejected, and an echoed marker shows up inside that quote without
anything having executed) - checking for a whole, standalone output
LINE fixed that false-positive direction, but the backtick case shows
the opposite failure mode exists too: a command that runs but produces
no output at all. The file-based check catches both; it is therefore
the only way any of these cases now gets checked.
"""

from __future__ import annotations

import os
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
# variable, and whatever a caller passes at invocation time is invisible
# to a check that only reads the recipe's own literal text. The recipe
# therefore validates the value itself, as a separate shell invocation
# preceding `docker run` (see the module docstring).
#
# PR #173 review, 2-й круг: `$(LOCK_UPGRADE)` сам по себе больше НЕ
# подставляется в текст рецепта - цель экспортирует `LOCK_UPGRADE_VALUE`
# как переменную ОКРУЖЕНИЯ (`lock-backend: export LOCK_UPGRADE_VALUE :=
# $(LOCK_UPGRADE)`), и рецепт читает её как `$$LOCK_UPGRADE_VALUE`
# (ссылка на переменную shell, не код). Симуляция здесь обязана делать
# то же самое: извлечённый текст рецепта больше НЕ подвергается
# текстовой замене `$(LOCK_UPGRADE)` -> значение (этой подстроки в
# рецепте больше нет вовсе) - вместо этого тестовое значение передаётся
# РЕАЛЬНОЙ переменной окружения `LOCK_UPGRADE_VALUE` самому `sh`, точно
# так же, как `make`/`docker run -e` передают её в жизни. `$$` -> `$`
# make всё равно сводит текстуально, независимо от переменных окружения
# - это не изменилось.


def _lock_backend_validation_script(makefile_text: str) -> str:
    lines = makefile_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("lock-backend:"):
            # Цель объявлена ДВУМЯ идущими подряд строками -
            # `lock-backend: export LOCK_UPGRADE_VALUE := ...` (целевая
            # переменная, без табуляции) и затем бare `lock-backend:`
            # перед самим рецептом. Обе начинаются с "lock-backend:" -
            # берём позицию ПОСЛЕ ПОСЛЕДНЕЙ такой строки, не первой,
            # иначе старт попадёт на вторую строку заголовка, а не на
            # первую строку рецепта с табуляцией.
            start = i + 1
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
    script = script.replace("$$", "$")
    return script


def _run_lock_upgrade_validation(lock_upgrade_value: str) -> subprocess.CompletedProcess[str]:
    makefile_text = _MAKEFILE_PATH.read_text(encoding="utf-8")
    script = _lock_backend_validation_script(makefile_text)
    env = {**os.environ, "LOCK_UPGRADE_VALUE": lock_upgrade_value}
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=env)


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


# --- PR #173 review, 2-й круг: проверка ПО ПОБОЧНОМУ ЭФФЕКТУ, не по тексту
# --- вывода -----------------------------------------------------------------
#
# Прежняя редакция этого теста (1-й круг) проверяла, что маркер
# инъекции не появился СВОЕЙ строкой в stdout/stderr - этого достаточно
# для `echo MARKER`, но НЕ для `touch <файл>`: успешный `touch` не
# печатает НИЧЕГО. Проверка по тексту вывода объявила бы находку этого
# круга (`-P a==\`touch /tmp/PWNED\``) безопасной, потому что ни строка
# `LOCK_UPGRADE_INJECTION_SHOULD_NOT_RUN`, ни что-либо похожее не
# появилось бы в выводе - при этом файл создавался (см. докстринг
# модуля). Текст вывода отвечает на вопрос «что процитировал отказ», не
# «что исполнилось» - для backtick/`$(...)`-инъекции эти два вопроса
# расходятся. Проверка ниже задаёт настоящий вопрос прямо: пытается
# создать файл в СВЕЖЕМ временном каталоге (`tmp_path`, свой на каждый
# параметризованный случай - не общий путь, который могла бы оставить
# позади другая инъекция) и утверждает, что файла нет - это верно и для
# случаев, которые что-то печатают (`echo`), и для тех, что не печатают
# ничего (`touch`), поэтому один и тот же механизм проверки годится для
# всех случаев сразу, без двух разных критериев успеха.
#
# Семь случаев прежнего теста пересмотрены на этот же вопрос («что они
# доказывают: отказ или отсутствие исполнения») и объединены с новым
# восьмым (голые обратные кавычки до любого отказа) под одной проверкой:
# шесть несут `touch {marker}` вместо `echo MARKER` и проверяются по
# файлу; пробел-в-кавычках не имеет исполняемой нагрузки вовсе (он про
# допустимые символы, не про инъекцию) и оставлен с проверкой только
# кода возврата, без утверждения про файл.


@_skip_without_full_checkout
@pytest.mark.parametrize(
    "lock_upgrade_template",
    [
        # Обратные кавычки голые - находка ЭТОГО круга: исполняются при
        # разборе `for tok in $(LOCK_UPGRADE)` ДО того, как до токена
        # добирается хоть один образец `case`, независимо от вердикта
        # валидации (см. докстринг модуля).
        "-P a==`touch {marker}`",
        # $(...) - тот же эффект, другой синтаксис подстановки команд.
        "-P a==$(touch {marker})",
        # Кавычка - находка 1-го круга: вырывалась из `bash -c "..."`
        # вызова `pip-compile`, не из цикла валидации.
        '-P a==1" && touch {marker} && echo "',
        # && / ; / | - три разных способа дописать вторую команду.
        "-P pkg==1 && touch {marker}",
        "-P pkg==1; touch {marker}",
        "-P pkg==1 | touch {marker}",
    ],
)
def test_lock_upgrade_validation_blocks_shell_injection(
    lock_upgrade_template: str, tmp_path
) -> None:
    marker = tmp_path / "SHOULD_NOT_EXIST"
    lock_upgrade_value = lock_upgrade_template.format(marker=marker)

    result = _run_lock_upgrade_validation(lock_upgrade_value)

    assert not marker.exists(), (
        f"LOCK_UPGRADE_VALUE={lock_upgrade_value!r} создал файл-маркер - "
        f"инъекция исполнилась независимо от вердикта валидации. "
        f"returncode={result.returncode} stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert result.returncode != 0, (
        f"LOCK_UPGRADE_VALUE={lock_upgrade_value!r} должна была быть "
        f"отвергнута, прошла с кодом {result.returncode}"
    )


@_skip_without_full_checkout
def test_lock_upgrade_validation_rejects_space_inside_a_quoted_token() -> None:
    # Пробел внутри кавычек - делает токен, не совпадающий по форме с
    # распознаваемым -P/значение, но принимавшийся прежним (1-й круг)
    # образцом `?*==?*`, который пробел не отличал от любого другого
    # символа. Без исполняемой нагрузки (не инъекция, а вопрос
    # допустимых символов) - проверяется только код возврата.
    result = _run_lock_upgrade_validation('-P "pkg==1 2"')

    assert result.returncode != 0, (
        f"должна была быть отвергнута, прошла с кодом {result.returncode}"
    )
