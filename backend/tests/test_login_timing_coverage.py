"""`FR-AUTH-002/14` (T-M1-41): every top-level `if` branch in
`local_service.authenticate_local` that can raise `AuthenticationError`
must pay the fixed-cost comparison (`verify_against_dummy_hash` in its
body, or `verify_password` in its own condition) unless its condition is
named in `_ALLOWED_WITHOUT_FIXED_COST` with a reason - discovered by
parsing the function with `ast`, same style as
`test_password_normalization_coverage.py` (AST, not substring: a
docstring mentioning either call by name must not count as a real call,
same self-reference class of bug that check was built to avoid).

Without this, a new `raise AuthenticationError(...)` added later to a
branch that returns before any fixed-cost comparison would silently
reopen the timing channel `FR-AUTH-002/14` closes today - the exact
regression class T-M1-41 exists to prevent from being invisible.

`_ALLOWED_WITHOUT_FIXED_COST` is keyed by the branch's own condition,
unparsed via `ast.unparse` (not a line number, which shifts on unrelated
edits) - same spirit as `_PUBLIC_ENDPOINTS`/`_ALLOWED_WITHOUT_OWN_CALL`
elsewhere in this suite: an exception is named with a reason, visible in
a diff, never a silent carve-out in the matching logic. Three entries
exist today, all decided in requirements.md decision #26:
  - empty/whitespace password and the 72-byte-limit rejection depend on
    the SUBMITTED PASSWORD, not the account - out of FR-AUTH-002/14's
    scope entirely;
  - the account-lockout branch answers a DIFFERENT message
    ("Account temporarily locked...") that already discloses the
    account's existence through its text - equalizing its timing on top
    of an already-disclosed fact would be decorative, not protective.

Honest boundary (rule 8, CLAUDE.md): this confirms a fixed-cost call
exists in a qualifying branch's body or condition - not that the branch
is reached for every account state that should trigger it, and not that
the timing is actually equal end to end (that is a one-time, run-by-hand
measurement, not a CI assertion - see the commit body: this suite
deliberately carries no timing assertions, per T-M1-54). Behavioral
coverage (the comparison actually runs, the message/status stay
unchanged) is `tests/modules/auth/test_login_timing_equalization.py`,
not this file.

This file checks STRUCTURE only and does not check TIME at all. The one
measurement that was taken (PR #176 review, T-M2-09: the branch for an
existing account with a wrong password pays an extra UPDATE + COMMIT and,
since T-M2-09, one SELECT of the lockout parameters, which the "no such
login" branch does not) found a median difference of about 1-2 ms against
a ~236 ms bcrypt cost. The SELECT's own share is NOT established by that
difference (its standard error, 0.2-0.7 ms, is of the order of the share);
it was measured directly: get_rules for two keys, median 0.23-0.24 ms,
against 0.13-0.14 ms for SELECT 1 on the same connection (three runs of
3000 alternating calls). Time is observable by a caller only a limited
number of times per lockout window (max_failed_login_attempts; samples
become available again once the lockout expires). The numbers and the
method are in the T-M2-09 entry of docs/tasks.md. A structural check cannot
see that difference, and that is the honest limit of this file, not a defect
in it.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from app.main import lifespan
from app.modules.auth.local_service import authenticate_local

_FIXED_COST_CALL_NAMES = frozenset({"verify_against_dummy_hash", "verify_password"})

# Обоснование у каждой записи обязательно - ключ - это ast.unparse самого
# условия ветки, не номер строки, который сдвигается при несвязанных
# правках.
_ALLOWED_WITHOUT_FIXED_COST: dict[str, str] = {
    "not password or not password.strip()": (
        "FR-AUTH-002/14 (requirements.md, раздел 13, п. 26): зависит от "
        "присланного пароля, не от учётной записи - вне объёма критерия"
    ),
    "exceeds_bcrypt_input_limit(password)": (
        "FR-AUTH-002/14 (раздел 13, п. 26): зависит от присланного пароля "
        "(его длины после NFC), не от учётной записи - вне объёма критерия"
    ),
    "account.locked_until is not None and account.locked_until > utcnow()": (
        "FR-AUTH-002/14 (раздел 13, п. 26): сообщение о блокировке уже "
        "раскрывает существование учётной записи текстом, без обращения к "
        "времени ответа - сознательное решение, выравнивание было бы "
        "декоративным, не защитой"
    ),
}


def _calls_any_fixed_cost_function(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Name) and func.id in _FIXED_COST_CALL_NAMES:
            return True
        if isinstance(func, ast.Attribute) and func.attr in _FIXED_COST_CALL_NAMES:
            return True
    return False


def _raises_authentication_error(if_node: ast.If) -> bool:
    for node in ast.walk(if_node):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        exc = node.exc
        call_func = exc.func if isinstance(exc, ast.Call) else None
        if isinstance(call_func, ast.Name) and call_func.id == "AuthenticationError":
            return True
    return False


def _top_level_if_branches(fn: object) -> list[ast.If]:
    source = textwrap.dedent(inspect.getsource(fn))
    function_def = ast.parse(source).body[0]
    return [node for node in function_def.body if isinstance(node, ast.If)]


def test_every_account_dependent_exit_pays_the_fixed_cost_comparison() -> None:
    branches = _top_level_if_branches(authenticate_local)
    assert branches, (
        "обнаружение не нашло ни одного верхнеуровневого if в "
        "authenticate_local - проверка сломана"
    )

    missing = []
    for branch in branches:
        if not _raises_authentication_error(branch):
            continue
        condition_text = ast.unparse(branch.test)
        if condition_text in _ALLOWED_WITHOUT_FIXED_COST:
            continue
        pays_fixed_cost = _calls_any_fixed_cost_function(
            branch.test
        ) or _calls_any_fixed_cost_function(branch)
        if not pays_fixed_cost:
            missing.append(f"line {branch.lineno}: if {condition_text}")

    assert not missing, (
        "эти ветки authenticate_local раскрывают факт существования "
        "учётной записи по времени ответа, не вызывая verify_against_"
        f"dummy_hash/verify_password: {missing}"
    )


def test_startup_warms_the_dummy_hash_cache() -> None:
    """PR #165 review: без прогрева при старте первый вызов `verify_
    against_dummy_hash` за процесс платит дважды (строит фиктивный хэш
    И тут же сверяется с ним) - отличим от обычного «неверный пароль»
    почти вдвое, см. докстринг `core.security._dummy_password_hash`. Не
    вызывает `lifespan` целиком (это задело бы реальную БД через `ensure_
    bootstrap_admin` в обход изоляции фикстур тестов, по тому же
    принципу, по которому `test_deploy_from_scratch.py` вызывает `ensure_
    bootstrap_admin` напрямую, а не через `lifespan`) - проверяет по AST,
    что сам код `lifespan` зовёт прогрев, без побочных эффектов на БД."""
    source = textwrap.dedent(inspect.getsource(lifespan))
    function_def = ast.parse(source).body[0]

    calls_warm_up = any(
        (isinstance(node.func, ast.Name) and node.func.id == "warm_dummy_password_hash_cache")
        or (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "warm_dummy_password_hash_cache"
        )
        for node in ast.walk(function_def)
        if isinstance(node, ast.Call)
    )

    assert calls_warm_up, (
        "lifespan() больше не вызывает warm_dummy_password_hash_cache() - "
        "кэш фиктивного хэша не прогревается до первого запроса"
    )
