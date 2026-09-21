"""`password_policy.validate_password_strength` (T-M1-25/T-M1-38,
FR-AUTH-002 v1.8)."""

from __future__ import annotations

import pytest

from app.modules.auth import password_policy
from app.modules.auth.password_policy import WeakPasswordError, validate_password_strength

# 8 символов, все четыре класса, точно не входит в общий список
# (случайная строка) и без переданного контекста личности.
_STRONG_PASSWORD = "Zq7$mVx2"


def test_strong_password_with_no_context_is_accepted() -> None:
    validate_password_strength(_STRONG_PASSWORD)


def test_password_below_minimum_length_is_rejected_with_only_that_violation() -> None:
    # 6 символов, все четыре класса, не в списке — изолирует именно
    # правило длины от остальных.
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Ab1!Ab")

    assert len(exc_info.value.violations) == 1
    assert "8 characters" in exc_info.value.violations[0]


# --- Состав символов (FR-AUTH-002 v1.8) -------------------------------------


def test_password_with_only_lowercase_letters_is_rejected() -> None:
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("qwertyui")

    assert any("three of" in v for v in exc_info.value.violations)


def test_password_meeting_composition_can_still_be_a_common_password() -> None:
    # «Qwerty1» проходит правило состава символов (буквы обоих
    # регистров + цифра — три класса из четырёх), но входит в список
    # распространённых паролей — отклоняется ИМ, а не составом.
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Qwerty1")

    assert not any("three of" in v for v in exc_info.value.violations)
    assert any("common" in v for v in exc_info.value.violations)


# --- Список распространённых паролей ----------------------------------------


def test_cyrillic_common_password_is_rejected() -> None:
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Пароль1")

    assert any("common" in v for v in exc_info.value.violations)


def test_cyrillic_keyboard_layout_pattern_is_rejected() -> None:
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Йцукен123")

    assert any("common" in v for v in exc_info.value.violations)


# --- Логин / e-mail / ФИО ----------------------------------------------------


def test_password_containing_login_is_rejected() -> None:
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Vasiliev2026!", login="Vasiliev")

    assert any("name" in v for v in exc_info.value.violations)


def test_password_containing_email_local_part_is_rejected() -> None:
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Vasiliev2026!", email="vasiliev@company.local")

    assert any("name" in v for v in exc_info.value.violations)


def test_short_name_tokens_are_not_checked() -> None:
    # «Ли Ир Ир» — все токены короче 4 символов, правило про ФИО не
    # применяется вовсе; пароль должен пройти по этому критерию.
    validate_password_strength(_STRONG_PASSWORD, full_name="Ли Ир Ир")


def test_password_containing_transliterated_full_name_is_rejected() -> None:
    """ГЛАВНОЕ (PR review, ПРОВЕРКА п. 1): сотрудник «Иванов Иван
    Иванович» не может задать пароль «Ivanov2026!» — ФИО хранится
    кириллицей, пароль латиницей, без транслитерации сравнение не
    совпадёт НИ РАЗУ."""
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Ivanov2026!", full_name="Иванов Иван Иванович")

    assert any("name" in v for v in exc_info.value.violations)


def test_transliteration_is_required_to_catch_the_name(monkeypatch) -> None:
    """Отрицательный контроль для теста выше: без транслитерации
    (сравнение только "как есть") тот же пароль ПРОХОДИТ — то есть
    правило без транслитерации ничего не проверяет для реального
    случая кириллического ФИО и латинского пароля."""
    monkeypatch.setattr(password_policy, "_transliterate", lambda text: text)

    # Не должно поднять исключение — без транслитерации "ivanov2026!"
    # не содержит ни "иванов", ни "иван", ни "иванович" (кириллица).
    validate_password_strength("Ivanov2026!", full_name="Иванов Иван Иванович")


# --- Всё сразу ---------------------------------------------------------------


def test_all_violations_are_reported_at_once() -> None:
    """FR-AUTH-002 v1.8: «показывать все невыполненные требования
    сразу, а не первое из них». Пароль «иван» короче минимума, состоит
    из одного класса символов (строчные кириллические буквы) и
    совпадает с токеном ФИО — три разных нарушения одновременно, ни
    одно не входит в список распространённых паролей отдельно."""
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("иван", full_name="Иванов Иван Иванович")

    violations = exc_info.value.violations
    assert len(violations) == 3
    assert any("8 characters" in v for v in violations)
    assert any("three of" in v for v in violations)
    assert any("name" in v for v in violations)


# --- Контекст необязателен ----------------------------------------------------


def test_missing_context_only_skips_the_identity_rule() -> None:
    # Без email/login/full_name правило про личность не применяется —
    # остальные правила (здесь: состав символов) продолжают работать.
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("qwertyui")

    assert not any("name" in v for v in exc_info.value.violations)
