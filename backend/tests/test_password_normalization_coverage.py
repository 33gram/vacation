"""T-M1-42 (`FR-AUTH-002/13`): every module-level function anywhere under
`app/` that takes a parameter literally named `password` must contain a
REAL call to `normalize_password`, unless it is named in
`_ALLOWED_WITHOUT_OWN_CALL` with a reason - discovered by importing every
module under `app/` (same discovery-over-enumeration style as
`test_constraints_applied.py`'s file walk) and inspecting each module's
top-level functions, not by naming two files. PR #164 review: the
original version of this check only imported `app.core.security` and
`app.modules.auth.password_policy` - a real password-touching function
in any OTHER module (the next candidate is `T-M1-30`'s password-reset
module) would have passed through unscanned and unnormalized, without
turning anything red. Confirmed by running before fixing it: scanning
the whole `app/` tree surfaces functions this check never saw before -
`app.core.ldap_client.authenticate`, `app.modules.auth.ldap_service.
authenticate_via_ldap`, and `app.modules.auth.bootstrap._reject_
oversized_bootstrap_password`, alongside the two already known
(`local_service.authenticate_local` and `normalize_password` itself).

`_ALLOWED_WITHOUT_OWN_CALL` is populated by what this round's run
actually found, not a list written in advance - same spirit as
`_PUBLIC_ENDPOINTS`/`_KNOWN_GAPS` elsewhere in this suite: a function
belongs here only with a reason attached, visible in a diff, never as a
silent carve-out in the matching logic. Two reasons appear today:
  - the two LDAP functions compare a password against the directory
    server over a bind request - this application never hashes, stores,
    or byte-compares an LDAP password itself, so `FR-AUTH-002/13`
    (which governs LOCAL passwords) does not apply to them at all;
  - `authenticate_local` and `_reject_oversized_bootstrap_password`
    never touch password bytes themselves - each calls straight into
    `exceeds_bcrypt_input_limit`/`verify_password`, which already
    normalize, so they are covered transitively.
`normalize_password` is excluded by name for the same self-reference
reason as before: it has a `password` parameter because it IS the
normalizer, and its own body calls `unicodedata.normalize`, not itself.

Real-call detection is by `ast.Call`, not substring, since PR #164
review: a function's source text includes its docstring, and a
docstring that happens to quote `normalize_password(...)` (to explain
what the function does, say) satisfied a substring check without a real
call existing anywhere in the function's actual code - reproduced
before fixing it, see the commit body. Parsing the function's source
with `ast.parse` and walking only `ast.Call` nodes does not have this
problem: a docstring is a `Constant` string value to the parser, not
re-parsed as nested code, so text inside it can never masquerade as a
call.

Honest boundary (rule 8, CLAUDE.md): this confirms a REAL CALL to
`normalize_password` exists somewhere in a function's body - not that
its result is used instead of the original argument (`normalize_
password(password)` as a bare expression, result discarded, would still
satisfy this check and do nothing), not that the call happens before
the password is used for anything else, and not that normalization
behaves correctly end to end. Behavioral correctness (round-trip
hash/verify across NFC/NFD, policy-decision equivalence, the 72-byte
boundary, the bootstrap administrator) is covered by
`tests/modules/auth/test_password_normalization.py`, not by this file.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import textwrap
from pathlib import Path
from types import ModuleType

from app.core.security import normalize_password

_APP_ROOT = Path(__file__).resolve().parents[1] / "app"

_EXCLUDED_FUNCTION_NAMES = frozenset({"normalize_password"})

# Обоснование у каждой записи обязательно - запись без него не исключение,
# а необнаруженный пробел. Перечень - по факту прогона этого раунда
# ревью, не предугадан заранее.
_ALLOWED_WITHOUT_OWN_CALL: dict[str, str] = {
    "app.core.ldap_client.authenticate": (
        "LDAP, не локальный пароль: сверка происходит bind-запросом на "
        "стороне каталога, приложение никогда не хэширует и не хранит этот "
        "пароль - FR-AUTH-002/13 регулирует локальные пароли, к LDAP не "
        "относится вовсе"
    ),
    "app.modules.auth.ldap_service.authenticate_via_ldap": (
        "то же исключение уровнем выше - вызывает core.ldap_client."
        "authenticate, см. его обоснование"
    ),
    "app.modules.auth.local_service.authenticate_local": (
        "не трогает байты пароля сама - вызывает exceeds_bcrypt_input_limit "
        "и verify_password, обе нормализуют внутри себя; покрыта транзитивно"
    ),
    "app.modules.auth.bootstrap._reject_oversized_bootstrap_password": (
        "не трогает байты пароля сама - вызывает exceeds_bcrypt_input_limit, "
        "которая нормализует внутри себя; покрыта транзитивно"
    ),
}


def _discover_app_modules() -> list[ModuleType]:
    modules = []
    for path in sorted(_APP_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        dotted = ".".join(path.relative_to(_APP_ROOT.parent).with_suffix("").parts)
        modules.append(importlib.import_module(dotted))
    return modules


def _functions_with_password_parameter(module: ModuleType) -> list[object]:
    return [
        obj
        for name, obj in vars(module).items()
        if inspect.isfunction(obj)
        and obj.__module__ == module.__name__
        and name not in _EXCLUDED_FUNCTION_NAMES
        and "password" in inspect.signature(obj).parameters
    ]


def _calls_normalize_password(fn: object) -> bool:
    # ast.parse, не поиск подстроки `normalize_password(` в исходном
    # тексте: докстринг для парсера - это Constant-строка, а не код, так
    # что цитата в докстринге никогда не даёт здесь ложного срабатывания
    # (см. докстринг модуля, PR #164 review).
    source = textwrap.dedent(inspect.getsource(fn))
    function_def = ast.parse(source).body[0]
    for node in ast.walk(function_def):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "normalize_password":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "normalize_password":
            return True
    return False


# --- _calls_normalize_password само по себе ---------------------------


def test_docstring_mention_alone_does_not_count_as_a_real_call() -> None:
    # PR #164 review: именно этот пример воспроизводит дыру substring-
    # версии проверки - докстринг цитирует вызов, тела у вызова нет.
    def fake_hash_password(password):
        """Приводит пароль к NFC вызовом normalize_password(password), затем хэширует."""
        return password.upper()

    assert not _calls_normalize_password(fake_hash_password)


def test_real_call_in_the_function_body_is_detected() -> None:
    def fake_hash_password(password):
        password = normalize_password(password)
        return password.upper()

    assert _calls_normalize_password(fake_hash_password)


def test_every_password_accepting_function_normalizes_before_use() -> None:
    functions = [
        fn
        for module in _discover_app_modules()
        for fn in _functions_with_password_parameter(module)
    ]
    assert functions, (
        "обнаружение не нашло ни одной функции с параметром password под "
        "app/ - проверка сломана (должно быть минимум четыре: "
        "exceeds_bcrypt_input_limit, hash_password, verify_password, "
        "validate_password_strength)"
    )

    missing = [
        f"{fn.__module__}.{fn.__qualname__}"
        for fn in functions
        if f"{fn.__module__}.{fn.__qualname__}" not in _ALLOWED_WITHOUT_OWN_CALL
        and not _calls_normalize_password(fn)
    ]
    assert not missing, f"эти функции не вызывают normalize_password(...): {missing}"
