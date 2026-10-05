"""T-M1-42 (`FR-AUTH-002/13`): every function that receives a raw
`password` argument in `app/core/security.py` and
`app/modules/auth/password_policy.py` must call `normalize_password`
as part of its own body - discovered by inspecting each module's
top-level function signatures for a parameter literally named
`password`, not by an enumerated list a new function could silently
bypass by never being added to it (same discovery-over-enumeration
style as `test_endpoint_auth_coverage.py`/`test_constraints_applied.py`
- a new password-touching function has to actively dodge the scan, not
just go unmentioned in a list).

`local_service.authenticate_local`, `bootstrap.ensure_bootstrap_admin`,
and `router.change_password` are deliberately NOT scanned here: none of
them touches password bytes directly - each one only calls into the
four functions this check DOES scan
(`exceeds_bcrypt_input_limit`/`hash_password`/`verify_password`,
`validate_password_strength`), so they are covered transitively, not by
carrying their own call to `normalize_password`.

`normalize_password` itself is excluded from the scan by name: it also
has a parameter named `password` (unavoidably - it IS the normalizer),
and its own body calls `unicodedata.normalize`, not itself - including
it in the discovered set would make this check permanently red on a
correct implementation, the same self-reference class of bug this
project's other coverage checks have hit before.

Honest boundary (rule 8, CLAUDE.md): this confirms the call to
`normalize_password` is PRESENT in a function's source text, not that
normalization actually happens correctly end-to-end, in the right order
relative to everything else in that function, or that its result is
used instead of the original argument. Behavioral correctness (round-
trip hash/verify across NFC/NFD, policy-decision equivalence, the
72-byte boundary, the bootstrap administrator) is covered by
`tests/modules/auth/test_password_normalization.py`, not by this file.
"""

from __future__ import annotations

import inspect
from types import ModuleType

from app.core import security
from app.modules.auth import password_policy

_EXCLUDED_FUNCTION_NAMES = frozenset({"normalize_password"})


def _functions_with_password_parameter(module: ModuleType) -> list[object]:
    return [
        obj
        for name, obj in vars(module).items()
        if inspect.isfunction(obj)
        and obj.__module__ == module.__name__
        and name not in _EXCLUDED_FUNCTION_NAMES
        and "password" in inspect.signature(obj).parameters
    ]


def test_every_password_accepting_function_normalizes_before_use() -> None:
    functions = _functions_with_password_parameter(security) + _functions_with_password_parameter(
        password_policy
    )
    assert functions, (
        "обнаружение не нашло ни одной функции с параметром password в "
        "core/security.py или modules/auth/password_policy.py - проверка "
        "сломана (должно быть минимум четыре: exceeds_bcrypt_input_limit, "
        "hash_password, verify_password, validate_password_strength)"
    )

    missing = [
        f"{fn.__module__}.{fn.__qualname__}"
        for fn in functions
        if "normalize_password(" not in inspect.getsource(fn)
    ]
    assert not missing, f"эти функции не вызывают normalize_password(...): {missing}"
