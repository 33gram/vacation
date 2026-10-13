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
this checks the RECIPE'S TEXT, never its execution. It confirms the
seed step and the `--output-file` target name the same path, that
`--generate-hashes` is present, and that `--upgrade`/`-U` is absent -
all three by inspecting the shell command string `lock-backend` embeds
in the Makefile. It does NOT run `pip-compile` and does NOT confirm
that seeding actually prevents drift at any given moment - the pins
could still drift if, for example, the seeded file itself were already
wrong when `lock-backend` runs, or `pip-compile` changed its own
incremental-reuse behavior in some future version. Catching an ACTUAL
drift requires running `make lock-backend` and diffing pins, exactly
as this round did by hand (`--platform linux/amd64` container,
network fetch against PyPI) - not something to run on every CI
invocation of `pytest` for cost reasons, and not something a text
check can do without running the thing it is checking.
"""

from __future__ import annotations

import re
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
