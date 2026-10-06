"""`FR-AUTH-002/14` (T-M1-41): for response-time differences that depend
on the ACCOUNT (no record for the login, a record whose auth method is
LDAP, a record with no local password hash set), `authenticate_local`
must pay the same bcrypt cost an ordinary wrong password pays - a
fixed-cost comparison against a dummy hash (`core.security.
verify_against_dummy_hash`), not a short circuit that skips it.

These tests check the MECHANISM (the comparison actually ran), not a
stopwatch: T-M1-54 spent an entire task removing timing-window
dependence from this test suite, and bringing it back as `assert
elapsed < X` would be the same class of flake, only louder. The
observable signal is a call counter wrapped around the real
`verify_against_dummy_hash` (a spy, not a behavior mock - it still runs
for real), plus confirming the message and status stay identical to an
ordinary wrong password.
"""

from __future__ import annotations

import pytest

from app.core import security
from app.core.security import hash_password, warm_dummy_password_hash_cache
from app.modules.auth import local_service
from app.modules.auth.exceptions import AuthenticationError
from app.modules.auth.local_service import authenticate_local
from tests.modules.auth.factories import make_account, make_employee


def _bcrypt_cost_prefix(bcrypt_hash: str) -> str:
    # "$2b$12$<22-символьная соль><31-символьный хэш>" - первые три поля,
    # разделённые "$", несут версию алгоритма и cost-фактор, не зависят
    # от соли/самого пароля.
    return "$".join(bcrypt_hash.split("$")[:3])


def test_dummy_hash_shares_the_real_cost_factor() -> None:
    """Фиктивный хэш не захардкожен - получен тем же `hash_password`,
    что и настоящие пароли, поэтому смена cost-фактора bcrypt
    (`gensalt()`) меняет оба значения вместе, а не оставляет выравнивание
    косметическим. Проверено прогоном: оба хэша делят один и тот же
    префикс версии+cost-фактора bcrypt."""
    security._dummy_password_hash.cache_clear()
    dummy_hash = security._dummy_password_hash()

    assert _bcrypt_cost_prefix(dummy_hash) == _bcrypt_cost_prefix(hash_password("irrelevant"))


def test_warming_the_cache_populates_it_before_any_request() -> None:
    """PR #165 review: без прогрева первый вызов `verify_against_dummy_
    hash` за процесс строит фиктивный хэш ВНУТРИ себя (второй bcrypt,
    464.9 мс вместо 232-234 - см. докстринг `_dummy_password_hash`) -
    отличим от обычного «неверный пароль», который хэш никогда не
    строит, почти вдвое. `warm_dummy_password_hash_cache` обязана
    заполнить кэш ДО того, как что-либо обратится к `verify_against_
    dummy_hash` - проверяется напрямую, по состоянию кэша, а не по
    времени (T-M1-54: секундомер в тестах не возвращаем)."""
    security._dummy_password_hash.cache_clear()
    assert security._dummy_password_hash.cache_info().currsize == 0

    warm_dummy_password_hash_cache()

    assert security._dummy_password_hash.cache_info().currsize == 1


def _spy_on_dummy_hash_comparison(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"n": 0}
    original = local_service.verify_against_dummy_hash

    def _counting_wrapper(password: str) -> None:
        calls["n"] += 1
        original(password)

    monkeypatch.setattr(local_service, "verify_against_dummy_hash", _counting_wrapper)
    return calls


async def test_unknown_login_pays_the_fixed_cost_comparison(db_session, monkeypatch) -> None:
    calls = _spy_on_dummy_hash_comparison(monkeypatch)

    with pytest.raises(AuthenticationError) as exc_info:
        await authenticate_local(db_session, "does-not-exist@company.local", "whatever-password")

    assert calls["n"] == 1
    assert str(exc_info.value) == "Invalid login or password"


async def test_ldap_account_pays_the_fixed_cost_comparison(db_session, monkeypatch) -> None:
    calls = _spy_on_dummy_hash_comparison(monkeypatch)
    employee = await make_employee(db_session, email="ldap-timing@company.local")
    await make_account(db_session, employee_id=employee.id, auth_type="ldap", role="employee")

    with pytest.raises(AuthenticationError) as exc_info:
        await authenticate_local(db_session, "ldap-timing@company.local", "whatever-password")

    assert calls["n"] == 1
    assert str(exc_info.value) == "Invalid login or password"


async def test_missing_local_hash_pays_the_fixed_cost_comparison(db_session, monkeypatch) -> None:
    calls = _spy_on_dummy_hash_comparison(monkeypatch)
    employee = await make_employee(db_session, email="no-hash-timing@company.local")
    await make_account(db_session, employee_id=employee.id, auth_type="local", role="employee")

    with pytest.raises(AuthenticationError) as exc_info:
        await authenticate_local(db_session, "no-hash-timing@company.local", "whatever-password")

    assert calls["n"] == 1
    assert str(exc_info.value) == "Invalid login or password"


async def test_ordinary_wrong_password_does_not_use_the_dummy_hash_path(
    db_session, monkeypatch
) -> None:
    """Отрицательный контроль к самому механизму спая: обычный неверный
    пароль против РЕАЛЬНОГО хэша не должен проходить через фиктивный —
    у него есть свой хэш для сравнения. Если бы `verify_against_dummy_
    hash` вызывался и здесь, три теста выше ничего не доказывали бы о
    РАЗНИЦЕ между путями, только о том, что функция вызывается всегда."""
    from app.core.security import hash_password

    calls = _spy_on_dummy_hash_comparison(monkeypatch)
    employee = await make_employee(db_session, email="wrong-password-timing@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password("CorrectHorseBatteryStaple"),
    )

    with pytest.raises(AuthenticationError) as exc_info:
        await authenticate_local(
            db_session, "wrong-password-timing@company.local", "TotallyWrongPassword1!"
        )

    assert calls["n"] == 0
    assert str(exc_info.value) == "Invalid login or password"
