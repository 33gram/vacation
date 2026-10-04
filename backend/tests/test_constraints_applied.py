"""Every backend-project `pip install` in `.github/workflows/ci.yml` and
`backend/Dockerfile` carries `-c constraints.txt` (`T-M1-43`, `PR #162
review`) - a fourth install site added later without `-c constraints.txt`
would install by range, silently, and the whole point of pinning
disappears without anyone noticing. Same failure class as `T-M1-46` and
`department_service`'s `head_employee_id` invariant: a rule enforced in
several places with nothing checking all of them at once.

Same path-resolution guard as `test_ears_criteria_coverage.py`, for the
same reason - not inherited as a blanket property of every check in this
suite: `.github/workflows/ci.yml` lives above `backend/`, which the dev
container does not mount - only CI's full checkout does.
`backend/Dockerfile` by itself resolves fine inside the dev container
(it is under `backend/`), but the one test here that reads both files
together is gated on the one that does not resolve - as a decorator on
that single test, not a module-wide `pytestmark`: the pure-function unit
tests below touch no filesystem at all and run everywhere, including
locally, regardless of checkout depth. Do not treat
`test_dependency_pins.py`'s "no skipif needed" as a precedent for every
new check in this suite - that module's docstring explains its
skip-free-ness is specific to files living under `backend/`; the one
gated test here is not one of those.

Pure-function core, in the same spirit as `find_violations`
(`test_dependency_pins.py`): `find_unconstrained_project_installs` works
over TEXT, not a shell parser or a re-resolution of anything. It looks
for `pip install` lines whose install target is the backend project
itself (`.`, `-e .`, `".[dev]"`/`'.[dev]'` and quoting variants) and
flags any such line missing `-c constraints.txt`. `python -m pip
install --upgrade pip` and the `pip-tools` install inside `make
lock-backend` are not flagged - not because of an entry in an exceptions
list, but because neither install target matches the "this is the
backend project" pattern at all; there is nothing there to declare an
exception for.

`_ALLOWED_WITHOUT_CONSTRAINTS` exists for the same reason
`_KNOWN_GAPS`/`_PUBLIC_ENDPOINTS` do elsewhere in this suite: a genuine,
justified exception to "project installs need constraints.txt" would be
named here, visibly, in a diff - not special-cased inside the matching
logic. It is empty today; no such exception exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CI_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_DOCKERFILE_PATH = _REPO_ROOT / "backend" / "Dockerfile"

# Не pytestmark на весь модуль: только ПОСЛЕДНИЙ тест ниже читает
# .github/workflows/ci.yml и нуждается в пропуске внутри dev-контейнера
# (см. докстринг модуля). Остальные тесты - чистые юнит-тесты над
# find_unconstrained_project_installs на синтетических строках, файловой
# системы не касаются вовсе и обязаны выполняться всегда, в том числе
# локально.
_skip_without_full_checkout = pytest.mark.skipif(
    not _CI_WORKFLOW_PATH.is_file(),
    reason=(
        ".github/workflows/ci.yml не резолвится по вычисленному пути "
        "(parents[2] от backend/tests) - та же причина, что и у "
        "test_ears_criteria_coverage.py: внутри dev-контейнера backend "
        "смонтировано только поддерево backend/, а не весь репозиторий; "
        "в CI backend-tests запускает pytest напрямую на раннере с полным "
        "чекаутом, и путь резолвится верно"
    ),
)

_PROJECT_INSTALL_TARGET = re.compile(
    r"""
    (?:^|\s)                       # начало строки или пробел перед токеном
    (?:-e\s+)?                     # необязательный editable-флаг
    (?:
        '\.(?:\[[^']*\])?'         # '.' или '.[extra]' в одинарных кавычках
      | "\.(?:\[[^"]*\])?"         # '.' или '.[extra]' в двойных кавычках
      | \.(?:\[[\w,-]*\])?         # . или .[extra] без кавычек
    )
    (?=\s|;|$)
    """,
    re.VERBOSE,
)
_HAS_CONSTRAINTS_FLAG = re.compile(r"-c\s+constraints\.txt\b")

_ALLOWED_WITHOUT_CONSTRAINTS: dict[str, str] = {}


def find_unconstrained_project_installs(
    *, ci_yaml_text: str, dockerfile_text: str
) -> list[str]:
    violations: list[str] = []
    for label, text in (("ci.yml", ci_yaml_text), ("Dockerfile", dockerfile_text)):
        for line in text.splitlines():
            if "pip install" not in line:
                continue
            if not _PROJECT_INSTALL_TARGET.search(line):
                continue
            stripped = line.strip()
            if stripped in _ALLOWED_WITHOUT_CONSTRAINTS:
                continue
            if _HAS_CONSTRAINTS_FLAG.search(line):
                continue
            violations.append(f"{label}: {stripped}")
    return violations


# --- find_unconstrained_project_installs само по себе ----------------------


def test_upgrading_pip_itself_is_not_a_project_install() -> None:
    violations = find_unconstrained_project_installs(
        ci_yaml_text="run: python -m pip install --upgrade pip\n",
        dockerfile_text="",
    )

    assert violations == []


def test_bare_dot_install_without_constraints_is_flagged() -> None:
    violations = find_unconstrained_project_installs(
        ci_yaml_text="run: pip install -e .\n",
        dockerfile_text="",
    )

    assert len(violations) == 1
    assert "pip install -e ." in violations[0]


def test_quoted_dev_extra_install_without_constraints_is_flagged() -> None:
    violations = find_unconstrained_project_installs(
        ci_yaml_text="",
        dockerfile_text='RUN pip install --no-cache-dir -e ".[dev]"\n',
    )

    assert len(violations) == 1


def test_install_with_constraints_flag_is_not_flagged() -> None:
    violations = find_unconstrained_project_installs(
        ci_yaml_text='run: pip install -e ".[dev]" -c constraints.txt\n',
        dockerfile_text="RUN pip install --no-cache-dir -e . -c constraints.txt\n",
    )

    assert violations == []


def test_declared_exception_suppresses_a_matching_line(monkeypatch) -> None:
    line = "run: pip install -e ."
    assert find_unconstrained_project_installs(
        ci_yaml_text=f"{line}\n", dockerfile_text=""
    ) == [f"ci.yml: {line}"]

    monkeypatch.setitem(
        _ALLOWED_WITHOUT_CONSTRAINTS, line, "тестовое обоснование, не настоящее исключение"
    )

    assert (
        find_unconstrained_project_installs(ci_yaml_text=f"{line}\n", dockerfile_text="") == []
    )


# --- против настоящих .github/workflows/ci.yml и backend/Dockerfile -------


@_skip_without_full_checkout
def test_every_project_pip_install_uses_constraints_file() -> None:
    violations = find_unconstrained_project_installs(
        ci_yaml_text=_CI_WORKFLOW_PATH.read_text(encoding="utf-8"),
        dockerfile_text=_DOCKERFILE_PATH.read_text(encoding="utf-8"),
    )

    assert not violations, "\n".join(violations)
