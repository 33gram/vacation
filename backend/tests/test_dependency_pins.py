"""`backend/pyproject.toml`'s direct dependencies vs.
`backend/requirements.lock` (`T-M1-43`, hashed since `T-M1-55`) — catches
a dependency added to one file and forgotten in the other, without
re-resolving anything.

Deliberately NOT a re-resolution check: this does not run pip or any
resolver and does not recompute what the dependency graph should be. A
check that re-resolved and diffed against the lock file would go red on
every unrelated PR the moment an upstream maintainer cut a new release
inside an already-declared range - a false positive on the release
schedule of a third party, not on anything this repository did. Instead
`find_violations` is a pure function over the two files' text: it checks
only that (1) every DIRECT dependency declared in `pyproject.toml`
(`project.dependencies` and `project.optional-dependencies.dev`) has a
line in `requirements.lock`, and (2) the pinned version there satisfies
the range `pyproject.toml` declares for it. Both conditions are checked
by parsing, never by re-deciding what the "right" version is.

`requirements.lock` lines look like `name==version` followed by a
line-continuation backslash, then indented `--hash=sha256:...`
continuation lines (`T-M1-55`, `pip-compile --generate-hashes`), not the
bare `name==version` lines `constraints.txt` had. `_PIN_LINE` still
matches correctly without any change: it anchors at line start and stops
the version capture at the first whitespace, which is exactly where the
trailing backslash begins: the hash lines themselves start with
indentation and never match `_PIN_LINE` at all. Verified by running the
full suite against the real,
hash-bearing file (see the real-file test below) - this is not a design
inherited unchanged from `constraints.txt` by assumption.

Honest boundary (rule 8, CLAUDE.md): this confirms presence and range-
membership of DIRECT dependencies only. It does not and cannot confirm
that `requirements.lock` is a COMPLETE transitive closure - a transitive
dependency could be missing from the file entirely and this check would
not notice, because it never walks the dependency graph, only the two
flat lists of names `pyproject.toml` and `requirements.lock` already give
it. Completeness of the HASHED closure is now additionally enforced by
`pip install --require-hashes` itself at install time (`T-M1-55`,
`test_hashed_lock_applied.py`): pip refuses to install anything not
listed with a hash, which is a stronger backstop than this test could
ever be - but that is `pip`'s guarantee, running, not a property this
pure-function test demonstrates. Measuring transitive completeness
independently of pip's own refusal is a job for an actual install-and-
compare run (`T-M1-43`/`T-M1-55` commit bodies: `pip install` against a
clean environment, then `pip freeze` diffed against `requirements.lock`),
not something this pure function can do without running pip itself.

Unlike `test_ears_criteria_coverage.py`, this module carries no
`pytest.mark.skipif` and needs none: both `pyproject.toml` and
`requirements.lock` live inside `backend/`, which the dev container
already mounts, so the path-resolution problem that forces that other
module's skip (`docs/requirements.md` living one level above `backend/`)
does not exist here. `tomllib` is stdlib (3.12+) and `packaging` arrives
transitively via pytest - both are already on the path in every
environment that can run this test at all. Do not add a `skipif` here by
analogy with that other module; there is nothing here for one to guard
against.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

_PIN_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)")


@dataclass(frozen=True)
class Violation:
    name: str
    reason: str

    def __str__(self) -> str:  # pragma: no cover - only used for assert messages
        return f"{self.name}: {self.reason}"


def _direct_requirement_specs(pyproject_text: str) -> list[str]:
    project = tomllib.loads(pyproject_text)["project"]
    specs = list(project.get("dependencies", []))
    specs += list(project.get("optional-dependencies", {}).get("dev", []))
    return specs


def _pinned_versions(lock_text: str) -> dict[str, str]:
    # Строки вида "    # via ..." (комментарии-аннотации pip-compile) и
    # "    --hash=sha256:..." (T-M1-55) пропускаются тем, что для них не
    # совпадает _PIN_LINE (начинается с пробела/`#`, не с имени пакета).
    # "name==version \" само совпадает: версия захватывается до первого
    # пробела, которым начинается " \" перед переносом на хэш-строки.
    pins: dict[str, str] = {}
    for line in lock_text.splitlines():
        match = _PIN_LINE.match(line)
        if not match:
            continue
        name, version = match.group(1), match.group(2)
        pins[canonicalize_name(name)] = version
    return pins


def find_violations(pyproject_text: str, lock_text: str) -> list[Violation]:
    pins = _pinned_versions(lock_text)
    violations: list[Violation] = []
    for spec in _direct_requirement_specs(pyproject_text):
        requirement = Requirement(spec)
        normalized_name = canonicalize_name(requirement.name)
        pinned_version = pins.get(normalized_name)
        if pinned_version is None:
            violations.append(Violation(requirement.name, "нет в requirements.lock"))
            continue
        if not requirement.specifier.contains(pinned_version, prereleases=True):
            violations.append(
                Violation(
                    requirement.name,
                    f"закреплённая версия {pinned_version} не входит в диапазон "
                    f"{requirement.specifier}, объявленный в pyproject.toml",
                )
            )
    return violations


# --- find_violations само по себе -----------------------------------------


def _pyproject_with(dependencies: list[str], dev: list[str] | None = None) -> str:
    dev_table = f"\n[project.optional-dependencies]\ndev = {dev!r}\n" if dev else ""
    return (
        "[project]\n"
        'name = "x"\n'
        'version = "0.0.0"\n'
        f"dependencies = {dependencies!r}\n" + dev_table
    )


def test_missing_dependency_is_reported_by_name() -> None:
    violations = find_violations(
        _pyproject_with(["foo>=1,<2"]),
        lock_text="",
    )

    assert [v.name for v in violations] == ["foo"]
    assert "нет в requirements.lock" in violations[0].reason


def test_version_outside_declared_range_is_reported_with_both_bounds() -> None:
    violations = find_violations(
        _pyproject_with(["foo>=1,<2"]),
        lock_text="foo==2.5.0\n",
    )

    assert len(violations) == 1
    assert violations[0].name == "foo"
    assert "2.5.0" in violations[0].reason
    assert ">=1" in violations[0].reason
    assert "<2" in violations[0].reason


def test_version_inside_declared_range_is_not_a_violation() -> None:
    violations = find_violations(
        _pyproject_with(["foo>=1,<2"]),
        lock_text="foo==1.5.0\n",
    )

    assert violations == []


def test_name_normalized_per_pep503_underscore_and_case() -> None:
    # PEP 503: "Foo_Bar" и "foo-bar" - один и тот же проектный идентификатор.
    violations = find_violations(
        _pyproject_with(["Foo_Bar>=1,<2"]),
        lock_text="foo-bar==1.0.0\n",
    )

    assert violations == []


def test_extras_on_direct_dependency_do_not_block_matching() -> None:
    violations = find_violations(
        _pyproject_with(
            [
                "uvicorn[standard]>=0.30,<0.31",
                "sqlalchemy[asyncio]>=2.0,<2.1",
                "celery[redis]>=5.4,<6",
            ]
        ),
        lock_text="uvicorn==0.30.6\nsqlalchemy==2.0.54\ncelery==5.6.3\n",
    )

    assert violations == []


def test_dev_optional_dependencies_are_checked_too() -> None:
    violations = find_violations(
        _pyproject_with(["foo>=1,<2"], dev=["bar>=3,<4"]),
        lock_text="foo==1.0.0\n",
    )

    assert [v.name for v in violations] == ["bar"]


def test_hash_annotated_lock_line_is_parsed_correctly() -> None:
    # T-M1-55: `pip-compile --generate-hashes` формат - версия на первой
    # строке до " \", хэши и "# via ..." на отдельных строках с отступом.
    # Докстринг модуля заявляет, что _PIN_LINE справляется без изменений
    # - этот тест проверяет это утверждение прогоном, а не рассуждением.
    violations = find_violations(
        _pyproject_with(["foo>=1,<2"]),
        lock_text=(
            "foo==1.5.0 \\\n"
            "    --hash=sha256:abc123 \\\n"
            "    --hash=sha256:def456\n"
            "    # via bar\n"
        ),
    )

    assert violations == []


# --- против настоящих backend/pyproject.toml и backend/requirements.lock ----

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_PYPROJECT_PATH = _BACKEND_ROOT / "pyproject.toml"
_LOCK_PATH = _BACKEND_ROOT / "requirements.lock"


def test_every_direct_dependency_is_pinned_within_its_declared_range() -> None:
    violations = find_violations(
        _PYPROJECT_PATH.read_text(encoding="utf-8"),
        _LOCK_PATH.read_text(encoding="utf-8"),
    )

    assert not violations, "\n".join(str(v) for v in violations)
