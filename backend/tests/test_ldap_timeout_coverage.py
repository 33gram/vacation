"""T-M1-58: every `ldap3.Server(...)`/`ldap3.Connection(...)` construction
anywhere under `app/` must carry its timeout-budget keyword
(`connect_timeout` for `Server`, `receive_timeout` for `Connection`),
unless named in `_ALLOWED_WITHOUT_BUDGET` with a reason - discovered by
walking the AST of every module under `app/`, not by naming the two
functions that happen to call them today (`app.core.ldap_client.
authenticate`/`test_connection`). Same discover-over-enumerate principle
as `test_password_normalization_coverage.py` (`T-M1-42`) and
`test_hashed_lock_applied.py` (`T-M1-55`): a third function added later
(a different search strategy for `T-M1-30`, say, or a connection opened
directly inside a router for some one-off diagnostic) would construct
its own `Server`/`Connection` and this task's budget would simply not
apply to it, with nothing turning red to say so - exactly the shape
`T-M1-42`'s own review round found and fixed for `normalize_password`,
reused here for the same reason.

Granularity is per TOP-LEVEL FUNCTION, not per bare call expression -
same reason as `test_password_normalization_coverage.py`'s `_ALLOWED_
WITHOUT_OWN_CALL`: a stable, nameable unit for the exceptions registry.
`ast.walk` over a function's own subtree already reaches calls nested
inside `with`/`try`/`if` blocks (ldap_client.py's two `Connection(...)`
calls inside `authenticate` are both nested inside `try` blocks, for
instance), so this is not a shallower scan than "every call in the
file" - it just names violations by the function that contains them,
not by a line number that shifts on every unrelated edit.

Real call detection is by `ast.Call`/`ast.keyword`, not substring -
same reason as `T-M1-42`, PR #164 review: a docstring or comment that
happens to mention `Server(host, connect_timeout=5)` to explain the
budget (this very module's own docstring does, elsewhere in this
sentence) is a `Constant` string to the parser, never re-parsed as
code, so it can never satisfy this check by accident.

Honest boundary (rule 8, CLAUDE.md), inherited from the same precedent
this module follows rather than closed here: like `test_password_
normalization_coverage.py`, this only discovers module-level functions
(`inspect.isfunction` on `vars(module)`) - a `Server`/`Connection` call
inside a CLASS METHOD would not be found, because no class under `app/`
constructs either today and this check was not asked to anticipate one
that does not exist. If a method ever does, this check needs widening
first, the same way `T-M1-42`'s review round widened its own scan from
two named modules to the whole `app/` tree only once a third real
caller existed. Also not caught: a budget passed via `**kwargs` spread
rather than a literal `connect_timeout=`/`receive_timeout=` keyword -
no call under `app/` does this today, and engineering around a
spreading style nothing in this repository uses would be guarding
against a hypothetical, not a measured gap.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import textwrap
from pathlib import Path
from types import ModuleType

_APP_ROOT = Path(__file__).resolve().parents[1] / "app"

# Server(...) -> connect_timeout=, Connection(...) -> receive_timeout= -
# T-M1-58: the two different hangs each flag bounds (see tests/core/
# test_ldap_client.py's module docstring for why they are different
# scenarios, not two names for the same thing).
_TRACKED_CALLS = {"Server": "connect_timeout", "Connection": "receive_timeout"}

# Обоснование у каждой записи обязательно - запись без него не исключение,
# а необнаруженный пробел (тот же принцип, что у `_ALLOWED_WITHOUT_OWN_
# CALL`/`_PUBLIC_ENDPOINTS` в соседних сторожах). Пусто сегодня: обе
# функции, создающие `Server`/`Connection` (`app.core.ldap_client.
# authenticate`/`test_connection`), несут оба параметра бюджета - нет
# ни одного исключения, которое нужно было бы объявить.
_ALLOWED_WITHOUT_BUDGET: dict[str, str] = {}


def _discover_app_modules() -> list[ModuleType]:
    modules = []
    for path in sorted(_APP_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        dotted = ".".join(path.relative_to(_APP_ROOT.parent).with_suffix("").parts)
        modules.append(importlib.import_module(dotted))
    return modules


def _module_level_functions(module: ModuleType) -> list[object]:
    return [
        obj
        for name, obj in vars(module).items()
        if inspect.isfunction(obj) and obj.__module__ == module.__name__
    ]


def find_calls_without_budget(fn: object) -> list[str]:
    """Возвращает описания вызовов `Server`/`Connection` в теле `fn`,
    не несущих свой обязательный keyword бюджета - `ast.walk` по
    поддереву функции, не по тексту целиком, так что вложенность
    (`with`/`try`/`if`) не имеет значения."""
    source = textwrap.dedent(inspect.getsource(fn))
    function_def = ast.parse(source).body[0]
    violations: list[str] = []
    for node in ast.walk(function_def):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            call_name = func.id
        elif isinstance(func, ast.Attribute):
            call_name = func.attr
        else:
            continue
        required_kw = _TRACKED_CALLS.get(call_name)
        if required_kw is None:
            continue
        has_budget = any(kw.arg == required_kw for kw in node.keywords)
        if not has_budget:
            violations.append(f"{call_name}(...) без {required_kw}= (строка {node.lineno})")
    return violations


# --- find_calls_without_budget само по себе ---------------------------------


def test_docstring_mention_alone_does_not_count_as_a_real_call() -> None:
    def fake_connect():
        """Открывает Server(host, connect_timeout=5) - на деле не вызывает."""
        return 1

    assert find_calls_without_budget(fake_connect) == []


def test_real_call_with_budget_is_not_flagged() -> None:
    def fake_connect():
        return Server("host", connect_timeout=5)  # noqa: F821

    assert find_calls_without_budget(fake_connect) == []


def test_real_call_without_budget_is_flagged() -> None:
    def fake_connect():
        return Server("host")  # noqa: F821

    violations = find_calls_without_budget(fake_connect)

    assert len(violations) == 1
    assert "Server(...) без connect_timeout=" in violations[0]


def test_nested_call_inside_try_block_is_still_found() -> None:
    def fake_connect():
        try:
            return Connection("server")  # noqa: F821
        except Exception:
            raise

    violations = find_calls_without_budget(fake_connect)

    assert len(violations) == 1
    assert "Connection(...) без receive_timeout=" in violations[0]


def test_unrelated_call_is_not_flagged() -> None:
    def fake_connect():
        return SomethingElse("host")  # noqa: F821

    assert find_calls_without_budget(fake_connect) == []


# --- против настоящего дерева app/ -------------------------------------------


def test_every_server_or_connection_construction_carries_its_timeout_budget() -> None:
    functions = [fn for module in _discover_app_modules() for fn in _module_level_functions(module)]
    assert functions, (
        "обнаружение не нашло ни одной функции под app/ - проверка "
        "сломана, модулей под app/ множество"
    )

    missing: dict[str, list[str]] = {}
    for fn in functions:
        qualname = f"{fn.__module__}.{fn.__qualname__}"
        if qualname in _ALLOWED_WITHOUT_BUDGET:
            continue
        violations = find_calls_without_budget(fn)
        if violations:
            missing[qualname] = violations

    assert not missing, f"эти функции создают Server/Connection без бюджета: {missing}"
