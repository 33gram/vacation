"""`password_policy.validate_password_strength` (T-M1-25/T-M1-38,
FR-AUTH-002 v1.9)."""

from __future__ import annotations

import pytest

from app.core.security import hash_password, verify_password
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


# --- Верхняя граница длины: усечение bcrypt (PR #140 review) ----------------


def test_password_exceeding_72_bytes_is_rejected_in_isolation() -> None:
    # 76 ASCII-байт (= 76 символов), все четыре класса, не в списке —
    # изолирует именно правило верхней границы от остальных.
    password = "Aa1!" * 19
    assert len(password.encode("utf-8")) == 76

    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength(password)

    assert len(exc_info.value.violations) == 1
    assert "72 bytes" in exc_info.value.violations[0]


def test_password_of_exactly_72_bytes_is_accepted() -> None:
    # Граница включительно: 72 байта — ещё ПОЛНОСТЬЮ участвуют в
    # хэше bcrypt, отклонять их нечего.
    password = "Aa1!" * 18
    assert len(password.encode("utf-8")) == 72

    validate_password_strength(password)


def test_cyrillic_password_under_72_characters_can_still_exceed_72_bytes() -> None:
    """Кириллица — 2 байта на символ в UTF-8: 42 символа здесь дают 84
    байта, хотя формально «меньше 72» по количеству СИМВОЛОВ. Проверка
    в символах эту находку ревью пропустила бы — граница именно в
    байтах (см. `MAX_PASSWORD_BYTES`)."""
    password = "Иванов" * 7
    assert len(password) == 42
    assert len(password.encode("utf-8")) == 84

    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength(password)

    assert any("72 bytes" in v for v in exc_info.value.violations)


def test_length_upper_bound_is_required_to_reject_the_overlong_password(monkeypatch) -> None:
    """Отрицательный контроль для трёх тестов выше: без проверки
    (`MAX_PASSWORD_BYTES` эффективно снят) 76-байтовый пароль, который
    больше ничего не нарушает, проходит — то есть закрывает находку
    именно эта проверка, а не какое-то из уже существующих правил."""
    monkeypatch.setattr(password_policy, "MAX_PASSWORD_BYTES", 10**6)

    validate_password_strength("Aa1!" * 19)  # не поднимает — без проверки принят


def test_overlong_password_that_would_collide_under_bcrypt_is_rejected() -> None:
    """ГЛАВНОЕ (PR #140 review, ПРОВЕРКА п. 1). bcrypt сам по себе
    остаётся усекающим — это неизменное поведение библиотеки
    (`core/security.py`), не то, что политика паролей может починить;
    альтернатива («предварительно хэшировать SHA-256») рассмотрена и
    отклонена (`requirements.md` §13, п. 17). Первая часть теста
    показывает: коллизия реальна на уровне bcrypt как таковом —
    5003-символьный пароль, его 72-байтовый префикс и тот же префикс с
    другим хвостом аутентифицируют один и тот же хэш. Вторая часть —
    почему это больше не эксплуатируется через приложение: длиннее
    72 байт пароль теперь нельзя ЗАДАТЬ вовсе (T-M1-38 подключает эту
    проверку на всех точках входа), а не только «неудобно менять»."""
    long_password = "A" * 5003
    password_hash = hash_password(long_password)
    prefix_72_bytes = long_password.encode("utf-8")[:72].decode("utf-8")
    prefix_with_different_tail = prefix_72_bytes + "DIFFERENT_TAIL_XYZ"

    assert verify_password(long_password, password_hash)
    assert verify_password(prefix_72_bytes, password_hash)
    assert verify_password(prefix_with_different_tail, password_hash)

    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength(long_password)
    assert any("72 bytes" in v for v in exc_info.value.violations)


# --- Состав символов (FR-AUTH-002 v1.9) -------------------------------------


def test_password_with_only_lowercase_letters_is_rejected() -> None:
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("qwertyui")

    assert any("three of" in v for v in exc_info.value.violations)


def test_password_with_trailing_space_is_rejected_by_composition() -> None:
    """PR #140 review: до правки пробел засчитывался как «специальный
    символ» («не буква и не цифра»), и "abcdefg1 " набирал три класса
    из четырёх (строчные, цифра, «спецсимвол» = пробел) без единого
    настоящего спецсимвола. Теперь пробел не в счёт — остаются только
    строчные буквы и цифра, два класса, состав не пройден. Отдельно
    от этого пароль нарушает и новый критерий «без пробелов» —
    показывать оба нарушения сразу и есть цель FR-AUTH-002 v1.8."""
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("abcdefg1 ")

    violations = exc_info.value.violations
    assert any("three of" in v for v in violations)
    assert any("whitespace" in v for v in violations)


def test_password_with_tab_is_rejected() -> None:
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Ab1!Ab\t12")

    assert any("whitespace" in v for v in exc_info.value.violations)


def test_password_with_non_breaking_space_is_rejected() -> None:
    # Неразрывный пробел (U+00A0) — прилетает при копировании пароля из
    # офисных редакторов, а не только с обычной клавиатуры.
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Ab1!Ab 12")

    assert any("whitespace" in v for v in exc_info.value.violations)


def test_numero_sign_counts_as_a_special_character() -> None:
    """PR #140 review: «не вводить перечень допустимых спецсимволов» —
    «№» не входит ни в один правдоподобный перечень, но обязан
    засчитываться как спецсимвол через отрицание («не буква, не цифра,
    не пробел»). Пароль принимается ЦЕЛИКОМ (не только состав) — 8
    символов, 4 класса, не в списке распространённых, без контекста
    личности."""
    validate_password_strength("Пароль1№")


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
