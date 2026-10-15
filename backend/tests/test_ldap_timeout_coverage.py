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

PR #172 review, 4-й круг: the same mechanism, extended to a second,
unrelated keyword - every `Server(...)` must also carry `get_info=`
explicitly (`_ALLOWED_WITHOUT_GET_INFO`, `find_server_calls_without_
get_info`), for the same reason the budget is enforced this way: a
third `Server(...)` call added later without `get_info=` would silently
inherit `ldap3`'s own default (`SCHEMA`), re-introducing the extra DSE/
schema exchanges this task measured and removed (see `ldap_client.
authenticate`'s docstring for the measurement). `get_info` is not a
timeout budget and is kept in its own exceptions registry and its own
check function rather than folded into `_TRACKED_CALLS`/`_ALLOWED_
WITHOUT_BUDGET` - a reader looking up why a function is exempt should
not have to guess which of two unrelated requirements the entry is
about. The AST-walking core (`_find_calls_missing_keyword`) is shared
between both checks - same mechanism, applied to a different call name/
keyword pair.

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
caller existed.

A budget passed via `**kwargs` spread (`Connection(server, **opts)`
where `opts` holds `receive_timeout`) is NOT a silent miss, the
opposite direction from the boundary above - verified by running, not
assumed: `find_calls_without_budget` reads a call's keywords as literal
`ast.keyword` nodes, so a `**opts` spread never matches `kw.arg ==
"receive_timeout"` and the call is flagged as missing the budget EVEN
WHEN the budget is actually there at runtime. A loud false positive on
something compliant, the same direction this module's sibling checks
(`test_hashed_lock_applied.py`) already accept elsewhere, not a silent
pass on something broken - no call under `app/` is written this way
today, so nothing in this repository actually trips it, but a real one
written this way would need an `_ALLOWED_WITHOUT_BUDGET` entry with a
reason, same as any other false positive this family produces.
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

# T-M1-58, PR #172 review (4-й круг): `get_info=` - отдельное требование,
# не таймаут-бюджет, но та же причина обнаружения вместо перечисления -
# `ldap3`'s дефолт `Server(get_info=SCHEMA)` читает DSE и схему на каждый
# bind (измерено - 2 лишних `search`-обмена на соединение, см. докстринг
# `ldap_client.authenticate`), и третий вызов `Server(...)`, добавленный
# позже без явного `get_info=`, унаследовал бы этот дефолт молча - тот же
# риск, что без `connect_timeout=`/`receive_timeout=` выше. Отдельный
# словарь исключений, не слияние с `_ALLOWED_WITHOUT_BUDGET`: это другое
# ключевое слово с другим обоснованием каждого исключения, и слияние
# скрыло бы, какого именно требования запись касается. Пусто сегодня:
# обе функции, создающие `Server` (`authenticate`/`test_connection`),
# несут `get_info=`.
_ALLOWED_WITHOUT_GET_INFO: dict[str, str] = {}


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


def _find_calls_missing_keyword(fn: object, target_call_name: str, required_kw: str) -> list[str]:
    """Общий обход: вызовы `target_call_name(...)` в теле `fn`, не
    несущие `required_kw=` - `ast.walk` по поддереву функции, не по
    тексту целиком, так что вложенность (`with`/`try`/`if`) не имеет
    значения. Один и тот же механизм используется и для таймаут-
    бюджета (`find_calls_without_budget`), и для `get_info=`
    (`find_server_calls_without_get_info`) - два независимых keyword-
    требования, не два разных способа их искать."""
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
        if call_name != target_call_name:
            continue
        has_kw = any(kw.arg == required_kw for kw in node.keywords)
        if not has_kw:
            violations.append(f"{call_name}(...) без {required_kw}= (строка {node.lineno})")
    return violations


def find_calls_without_budget(fn: object) -> list[str]:
    """Возвращает описания вызовов `Server`/`Connection` в теле `fn`,
    не несущих свой обязательный keyword бюджета."""
    violations: list[str] = []
    for call_name, required_kw in _TRACKED_CALLS.items():
        violations.extend(_find_calls_missing_keyword(fn, call_name, required_kw))
    return violations


def find_server_calls_without_get_info(fn: object) -> list[str]:
    """Возвращает описания вызовов `Server(...)` в теле `fn`, не
    несущих `get_info=` - без него `ldap3` молча выбирает свой дефолт
    `SCHEMA` (T-M1-58, PR #172 review 4-й круг)."""
    return _find_calls_missing_keyword(fn, "Server", "get_info")


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


def test_budget_passed_via_kwargs_spread_is_a_false_positive_not_a_miss() -> None:
    # Названо в докстринге модуля: ast.keyword не видит **opts, и вызов,
    # который на деле несёт бюджет через распаковку, всё равно флагуется
    # - громкое ложное срабатывание на совместимом коде, не тихий
    # пропуск. Закрепляет утверждение докстринга прогоном, не только
    # описывает его словами.
    def fake_connect():
        opts = {"connect_timeout": 5}
        return Connection(Server("host", **opts), **opts)  # noqa: F821

    violations = find_calls_without_budget(fake_connect)

    assert len(violations) == 2
    assert any("Server(...) без connect_timeout=" in v for v in violations)
    assert any("Connection(...) без receive_timeout=" in v for v in violations)


# --- find_server_calls_without_get_info само по себе -------------------------
#
# Тот же набор случаев, что у find_calls_without_budget выше (докстринг,
# реальный вызов с/без keyword, вложенность, **kwargs-спред) - для
# отдельного keyword'а, другим механизмом обнаружения не проверяется.


def test_get_info_docstring_mention_alone_does_not_count_as_a_real_call() -> None:
    def fake_connect():
        """Открывает Server(host, get_info="NONE") - на деле не вызывает."""
        return 1

    assert find_server_calls_without_get_info(fake_connect) == []


def test_get_info_real_call_with_it_is_not_flagged() -> None:
    def fake_connect():
        return Server("host", get_info="NONE")  # noqa: F821

    assert find_server_calls_without_get_info(fake_connect) == []


def test_get_info_real_call_without_it_is_flagged() -> None:
    def fake_connect():
        return Server("host")  # noqa: F821

    violations = find_server_calls_without_get_info(fake_connect)

    assert len(violations) == 1
    assert "Server(...) без get_info=" in violations[0]


def test_get_info_nested_call_inside_try_block_is_still_found() -> None:
    def fake_connect():
        try:
            return Server("host")  # noqa: F821
        except Exception:
            raise

    violations = find_server_calls_without_get_info(fake_connect)

    assert len(violations) == 1
    assert "Server(...) без get_info=" in violations[0]


def test_get_info_connection_call_is_not_tracked() -> None:
    # get_info существует только у Server, не у Connection - вызов
    # Connection(...) без get_info= не должен флагаться этой проверкой,
    # у самого ldap3 нет такого параметра на Connection.
    def fake_connect():
        return Connection("server")  # noqa: F821

    assert find_server_calls_without_get_info(fake_connect) == []


def test_get_info_passed_via_kwargs_spread_is_a_false_positive_not_a_miss() -> None:
    def fake_connect():
        opts = {"get_info": "NONE"}
        return Server("host", **opts)  # noqa: F821

    violations = find_server_calls_without_get_info(fake_connect)

    assert len(violations) == 1
    assert "Server(...) без get_info=" in violations[0]


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


def test_every_server_construction_carries_get_info() -> None:
    functions = [fn for module in _discover_app_modules() for fn in _module_level_functions(module)]
    assert functions, (
        "обнаружение не нашло ни одной функции под app/ - проверка "
        "сломана, модулей под app/ множество"
    )

    missing: dict[str, list[str]] = {}
    for fn in functions:
        qualname = f"{fn.__module__}.{fn.__qualname__}"
        if qualname in _ALLOWED_WITHOUT_GET_INFO:
            continue
        violations = find_server_calls_without_get_info(fn)
        if violations:
            missing[qualname] = violations

    assert not missing, f"эти функции создают Server без get_info=: {missing}"
