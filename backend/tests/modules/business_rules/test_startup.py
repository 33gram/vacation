"""Стартовая проверка параметров в `lifespan` (T-M2-09, п. 2(б)): приложение
НЕ поднимается, если обязательного ключа нет, и называет его.

`lifespan` запускается целиком, но на тестовой сессии с откатом и с
подмененными прогревом хэша и созданием bootstrap-администратора: оба — не
предмет этого теста и трогали бы реальную БД (см. `test_login_timing_
coverage.py`, который по той же причине проверяет `lifespan` по AST).
"""

from __future__ import annotations

import contextlib

import pytest
from sqlalchemy import text

import app.main as main
from app.modules.business_rules.service import BusinessRulesConfigError


@pytest.fixture
def patched_lifespan(monkeypatch: pytest.MonkeyPatch, db_session):
    @contextlib.asynccontextmanager
    async def _session_context():
        yield db_session

    async def _no_bootstrap(_session) -> None:
        return None

    monkeypatch.setattr(main, "get_session_factory", lambda: _session_context)
    monkeypatch.setattr(main, "ensure_bootstrap_admin", _no_bootstrap)
    monkeypatch.setattr(main, "warm_dummy_password_hash_cache", lambda: None)


async def test_the_application_starts_when_every_rule_is_present(patched_lifespan) -> None:
    async with main.lifespan(main.app):
        pass


async def test_the_application_refuses_to_start_naming_the_missing_key(
    patched_lifespan, db_session
) -> None:
    await db_session.execute(
        text("DELETE FROM business_rules_config WHERE key = 'password_min_length'")
    )

    with pytest.raises(BusinessRulesConfigError) as excinfo:
        async with main.lifespan(main.app):
            pass

    assert "password_min_length" in str(excinfo.value)


async def test_the_application_refuses_to_start_with_a_garbage_time_zone(
    patched_lifespan, db_session
) -> None:
    # T-M2-34: форма верна, и CHECK базы её пропускает; зоны нет - это видит только сервис.
    await db_session.execute(
        text(
            "UPDATE business_rules_config SET value = 'Europe/Moskva' "
            "WHERE key = 'business_timezone'"
        )
    )

    with pytest.raises(BusinessRulesConfigError) as excinfo:
        async with main.lifespan(main.app):
            pass

    assert "business_timezone" in str(excinfo.value)
