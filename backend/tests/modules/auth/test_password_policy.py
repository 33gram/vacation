"""`password_policy.validate_password_strength` (T-M1-25/T-M1-38,
FR-AUTH-002 v1.9) и коды/подсказки парольной политики (T-M1-45)."""

from __future__ import annotations

import bcrypt
import pytest

from app.core import security
from app.core.security import BCRYPT_MAX_INPUT_BYTES, exceeds_bcrypt_input_limit, hash_password
from app.modules.auth import password_policy
from app.modules.auth.password_policy import WeakPasswordError, validate_password_strength

# 8 символов, все четыре класса, точно не входит в общий список
# (случайная строка) и без переданного контекста личности.
_STRONG_PASSWORD = "Zq7$mVx2"


def _violation_codes(exc: WeakPasswordError) -> set[str]:
    """Тесты проверяют КОД нарушения (T-M1-45), а не текст сообщения —
    сообщение теперь на русском (NFR-LOC-001) и меняется при
    переформулировке, а код — стабильный контракт с интерфейсом."""
    return {v.code for v in exc.violations}


def test_strong_password_with_no_context_is_accepted() -> None:
    validate_password_strength(_STRONG_PASSWORD)


def test_password_below_minimum_length_is_rejected_with_only_that_violation() -> None:
    # 6 символов, все четыре класса, не в списке — изолирует именно
    # правило длины от остальных.
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Ab1!Ab")

    assert len(exc_info.value.violations) == 1
    assert exc_info.value.violations[0].code == password_policy.VIOLATION_TOO_SHORT


# --- T-M1-45: коды нарушений и подсказки строятся из одного источника ------


def test_password_policy_codes_and_hints_are_the_same_set() -> None:
    """ГЛАВНЫЙ тест задачи (T-M1-45). Набор кодов, которые может
    поднять `validate_password_strength` (`PASSWORD_POLICY_VIOLATION_
    CODES`), и набор кодов, которые перечисляет `get_password_policy_
    hints()` (данные для `GET /api/auth/password-policy`), обязаны
    быть ОДНИМ И ТЕМ ЖЕ множеством. Именно расхождение этих двух
    списков — критерий без подсказки или подсказка без критерия — и
    есть та ошибка, ради которой заведена задача: интерфейс решает,
    какое нарушение показать, по коду из подсказки, и код, которого
    там нет, отобразить нечем."""
    hint_codes = {hint.code for hint in password_policy.get_password_policy_hints()}
    assert hint_codes == set(password_policy.PASSWORD_POLICY_VIOLATION_CODES)


def test_violation_message_matches_the_policy_hint_text() -> None:
    """Не только код, но и САМ ТЕКСТ нарушения обязан совпадать с
    текстом соответствующей подсказки: оба берутся из одного словаря
    (`_password_policy_messages`), поэтому подмена текста в одном месте
    без другого структурно невозможна, а не только по соглашению."""
    hints_by_code = {
        hint.code: hint.message for hint in password_policy.get_password_policy_hints()
    }

    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("qwertyui")  # только состав символов нарушен

    violation = exc_info.value.violations[0]
    assert violation.code == password_policy.VIOLATION_INSUFFICIENT_CHARACTER_CLASSES
    assert violation.message == hints_by_code[violation.code]


def test_get_password_policy_hints_substitutes_min_length() -> None:
    # T-M1-45: минимальная длина — бизнес-параметр (T-M2-09), подсказка
    # не хардкодит его, а подставляет переданное значение в текст.
    hints = {
        hint.code: hint.message for hint in password_policy.get_password_policy_hints(
            min_length=12
        )
    }
    assert "12" in hints[password_policy.VIOLATION_TOO_SHORT]


# --- Верхняя граница длины: усечение bcrypt (PR #140 review) ----------------


def test_password_exceeding_72_bytes_is_rejected_in_isolation() -> None:
    # 76 ASCII-байт (= 76 символов), все четыре класса, не в списке —
    # изолирует именно правило верхней границы от остальных.
    password = "Aa1!" * 19
    assert len(password.encode("utf-8")) == 76

    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength(password)

    assert len(exc_info.value.violations) == 1
    assert exc_info.value.violations[0].code == password_policy.VIOLATION_EXCEEDS_BYTE_LIMIT


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

    assert password_policy.VIOLATION_EXCEEDS_BYTE_LIMIT in _violation_codes(exc_info.value)


def test_length_upper_bound_is_required_to_reject_the_overlong_password(monkeypatch) -> None:
    """Отрицательный контроль для трёх тестов выше: без проверки
    (лимит эффективно снят) 76-байтовый пароль, который больше ничего
    не нарушает, проходит — то есть закрывает находку именно эта
    проверка, а не какое-то из уже существующих правил. PR #140 review
    (четвёртый круг): граница переехала в `core.security.
    BCRYPT_MAX_INPUT_BYTES`, `exceeds_bcrypt_input_limit` читает её из
    модуля `core.security` на каждый вызов — патчить нужно её, а не
    `password_policy.MAX_PASSWORD_BYTES` (тот теперь лишь однократный
    импорт значения на момент загрузки модуля, дальше с проверкой не
    связан)."""
    monkeypatch.setattr(security, "BCRYPT_MAX_INPUT_BYTES", 10**6)

    validate_password_strength("Aa1!" * 19)  # не поднимает — без проверки принят


def test_overlong_password_that_would_collide_under_bcrypt_is_rejected() -> None:
    """ГЛАВНОЕ (PR #140 review, ПРОВЕРКА п. 1). bcrypt сам по себе
    остаётся усекающим — это неизменное поведение библиотеки, не то,
    что политика паролей может починить; альтернатива («предварительно
    хэшировать SHA-256») рассмотрена и отклонена (`requirements.md`
    §13, п. 17). Первая часть теста показывает именно поведение bcrypt
    КАК ТАКОВОГО, напрямую через `bcrypt.hashpw`/`bcrypt.checkpw`, а не
    через приложенческий `hash_password` (PR #140 review, четвёртый
    круг: `hash_password` теперь сам отказывает на входе длиннее лимита
    — см. `test_hash_password_rejects_input_over_bcrypt_limit` ниже, —
    так что демонстрировать усечение через него уже нельзя): 5003-
    символьный пароль, его 72-байтовый префикс и тот же префикс с
    другим хвостом аутентифицируют один и тот же хэш. Вторая часть —
    почему это больше не эксплуатируется через приложение: длиннее
    72 байт пароль теперь нельзя ЗАДАТЬ вовсе (T-M1-38 подключает эту
    проверку на всех точках входа), а не только «неудобно менять»."""
    long_password = "A" * 5003
    password_hash = bcrypt.hashpw(long_password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    prefix_72_bytes = long_password.encode("utf-8")[:72].decode("utf-8")
    prefix_with_different_tail = prefix_72_bytes + "DIFFERENT_TAIL_XYZ"

    assert bcrypt.checkpw(long_password.encode("utf-8"), password_hash.encode("ascii"))
    assert bcrypt.checkpw(prefix_72_bytes.encode("utf-8"), password_hash.encode("ascii"))
    assert bcrypt.checkpw(
        prefix_with_different_tail.encode("utf-8"), password_hash.encode("ascii")
    )

    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength(long_password)
    assert password_policy.VIOLATION_EXCEEDS_BYTE_LIMIT in _violation_codes(exc_info.value)


def test_hash_password_rejects_input_over_bcrypt_limit() -> None:
    """Отрицательный контроль на сетку в `hash_password` (PR #140
    review, четвёртый круг): без неё этот тест обнаружил бы, что вход
    длиннее лимита ТИХО хэшируется усечённым — тем самым поведением,
    которое `test_overlong_password_that_would_collide_under_bcrypt_
    is_rejected` выше показывает как раз для bcrypt как такового. Здесь
    же проверяется именно сетка приложения, а не уже существующие
    проверки выше по стеку (`validate_password_strength`,
    `authenticate_local`, `_reject_oversized_bootstrap_password`) — вход
    ни одну из них не проходит."""
    long_password = "A" * (BCRYPT_MAX_INPUT_BYTES + 1)
    assert exceeds_bcrypt_input_limit(long_password)

    with pytest.raises(ValueError, match=r"72-byte"):
        hash_password(long_password)


# --- Состав символов (FR-AUTH-002 v1.9) -------------------------------------


def test_password_with_only_lowercase_letters_is_rejected() -> None:
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("qwertyui")

    assert (
        password_policy.VIOLATION_INSUFFICIENT_CHARACTER_CLASSES
        in _violation_codes(exc_info.value)
    )


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

    codes = _violation_codes(exc_info.value)
    assert password_policy.VIOLATION_INSUFFICIENT_CHARACTER_CLASSES in codes
    assert password_policy.VIOLATION_CONTAINS_WHITESPACE in codes


def test_password_with_tab_is_rejected() -> None:
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Ab1!Ab\t12")

    assert password_policy.VIOLATION_CONTAINS_WHITESPACE in _violation_codes(exc_info.value)


def test_password_with_non_breaking_space_is_rejected() -> None:
    # Неразрывный пробел (U+00A0) — прилетает при копировании пароля из
    # офисных редакторов, а не только с обычной клавиатуры.
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Ab1!Ab 12")

    assert password_policy.VIOLATION_CONTAINS_WHITESPACE in _violation_codes(exc_info.value)


def test_numero_sign_counts_as_a_special_character() -> None:
    """PR #140 review: «не вводить перечень допустимых спецсимволов» —
    «№» не входит ни в один правдоподобный перечень, но обязан
    засчитываться как спецсимвол через отрицание («не буква, не цифра,
    не пробел»). Пароль принимается ЦЕЛИКОМ (не только состав) — 8
    символов, 4 класса, не в списке распространённых, без контекста
    личности."""
    validate_password_strength("Пароль1№")


# --- Невидимые символы (PR #140 review, второй круг) ------------------------


def test_password_with_soft_hyphen_is_rejected() -> None:
    # U+00AD, категория Unicode Cf (форматирующий) — невидим при обычном
    # отображении, пользователь не сможет сознательно повторить его при
    # следующем вводе.
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Ab1!Ab­12")

    assert (
        password_policy.VIOLATION_CONTAINS_INVISIBLE_OR_CONTROL_CHARACTERS
        in _violation_codes(exc_info.value)
    )


def test_password_with_zero_width_joiner_is_rejected() -> None:
    # U+200D (ZWJ), категория Cf — использовался бы для маскировки: два
    # визуально одинаковых пароля с разным числом ZWJ внутри выглядели
    # бы одинаково, но были бы разными строками.
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Ab1!Ab‍12")

    assert (
        password_policy.VIOLATION_CONTAINS_INVISIBLE_OR_CONTROL_CHARACTERS
        in _violation_codes(exc_info.value)
    )


def test_password_with_bell_control_character_is_rejected() -> None:
    # U+0007 (BEL), категория Cc (управляющий) — не пробельный по
    # `str.isspace()`, отдельная категория символов от уже закрытых
    # пробельных.
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Ab1!Ab\x0712")

    assert (
        password_policy.VIOLATION_CONTAINS_INVISIBLE_OR_CONTROL_CHARACTERS
        in _violation_codes(exc_info.value)
    )


def test_invisible_character_rule_is_required_to_reject_zero_width_joiner(monkeypatch) -> None:
    """Отрицательный контроль: без вызова проверки невидимых символов
    (застаблено с помощью безусловно ложной функции) ZWJ ничем не
    отклоняется — пароль со всеми четырьмя классами и ZWJ внутри
    проходит целиком. Показывает, что закрывает находку именно эта
    проверка, а не что-то из уже существующих правил."""
    monkeypatch.setattr(
        password_policy, "_is_invisible_control_character", lambda _c: False
    )

    validate_password_strength("Ab1!Ab‍12")  # не поднимает — без проверки принят


def test_combining_acute_accent_does_not_count_as_a_special_character() -> None:
    """PR #140 review (второй круг): комбинирующий знак (категория
    Unicode M*, например U+0301 — комбинирующий акут) сам по себе НЕ
    отклоняется (обычная часть письменности многих языков), но и
    статуса спецсимвола не даёт. Без него пароль набирает только два
    класса (строчные + цифра) и не проходит состав — с ним, если бы
    комбинирующий знак ошибочно засчитывался за спецсимвол, состав был
    бы пройден (три класса из четырёх); проверяем, что этого не
    происходит."""
    password_without_mark = "password1"
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength(password_without_mark)
    assert (
        password_policy.VIOLATION_INSUFFICIENT_CHARACTER_CLASSES
        in _violation_codes(exc_info.value)
    )

    password_with_combining_mark = password_without_mark + "́"
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength(password_with_combining_mark)
    assert (
        password_policy.VIOLATION_INSUFFICIENT_CHARACTER_CLASSES
        in _violation_codes(exc_info.value)
    )


def test_combining_mark_exclusion_is_required_to_fail_composition(monkeypatch) -> None:
    """Отрицательный контроль для теста выше: без исключения категории
    M* из специального класса (застаблено с помощью безусловно ложной
    функции) тот же пароль с комбинирующим знаком набирал бы три класса
    из четырёх и проходил бы состав."""
    monkeypatch.setattr(password_policy, "_is_combining_mark", lambda _c: False)

    validate_password_strength("password1́")  # не поднимает — состав пройден


def test_password_meeting_composition_can_still_be_a_common_password() -> None:
    # «Qwerty1» проходит правило состава символов (буквы обоих
    # регистров + цифра — три класса из четырёх), но входит в список
    # распространённых паролей — отклоняется ИМ, а не составом.
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Qwerty1")

    codes = _violation_codes(exc_info.value)
    assert password_policy.VIOLATION_INSUFFICIENT_CHARACTER_CLASSES not in codes
    assert password_policy.VIOLATION_COMMON_PASSWORD in codes


# --- Список распространённых паролей ----------------------------------------


def test_cyrillic_common_password_is_rejected() -> None:
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Пароль1")

    assert password_policy.VIOLATION_COMMON_PASSWORD in _violation_codes(exc_info.value)


def test_cyrillic_keyboard_layout_pattern_is_rejected() -> None:
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Йцукен123")

    assert password_policy.VIOLATION_COMMON_PASSWORD in _violation_codes(exc_info.value)


# --- Логин / e-mail / ФИО ----------------------------------------------------


def test_password_containing_login_is_rejected() -> None:
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Vasiliev2026!", login="Vasiliev")

    assert password_policy.VIOLATION_CONTAINS_IDENTITY in _violation_codes(exc_info.value)


def test_password_containing_email_local_part_is_rejected() -> None:
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Vasiliev2026!", email="vasiliev@company.local")

    assert password_policy.VIOLATION_CONTAINS_IDENTITY in _violation_codes(exc_info.value)


def test_single_letter_name_tokens_are_not_checked() -> None:
    # Токены короче 2 символов (инициалы вида «О.») не проверяются
    # вовсе — ни как подстрока, ни как начало пароля.
    validate_password_strength(_STRONG_PASSWORD, full_name="О И П")


def test_short_name_token_at_start_of_password_is_rejected() -> None:
    """PR #140 review, ПРОВЕРКА п. 4: сотрудник «Ли Иван Петрович» не
    может задать пароль «Ли123456!» — короткая часть ФИО («Ли», 2
    символа) в начале пароля, ровно случай «фамилия+цифры»."""
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Ли123456!", full_name="Ли Иван Петрович")

    assert password_policy.VIOLATION_CONTAINS_IDENTITY in _violation_codes(exc_info.value)


def test_short_name_token_is_not_rejected_as_a_substring_elsewhere() -> None:
    """Симметрично тесту выше: тот же сотрудник МОЖЕТ задать
    «Lightning7!», хотя транслитерация «Ли» («li») и есть первые две
    буквы «Lightning» — после «li» идёт «g» (буква, не граница слова),
    так что это обычное слово, а не «фамилия+цифры». Правило не должно
    блокировать лишнего."""
    validate_password_strength("Lightning7!", full_name="Ли Иван Петрович")


def test_short_name_token_is_not_rejected_in_the_middle_of_the_password() -> None:
    # «Ли» — не в начале пароля (пароль начинается с «Super»): как
    # подстрока где угодно короткие токены не проверяются, иначе
    # заблокировали бы много обычных слов/паролей с той же подстрокой.
    validate_password_strength("SuperЛи123!", full_name="Ли Иван Петрович")


def test_long_name_token_is_rejected_anywhere_in_the_password() -> None:
    # «Петрович» (8 символов, >= _LONG_NAME_TOKEN_LENGTH) — запрещён в
    # любом месте пароля, не только в начале, в отличие от «Ли» выше.
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("SuperПетрович1!", full_name="Ли Иван Петрович")

    assert password_policy.VIOLATION_CONTAINS_IDENTITY in _violation_codes(exc_info.value)


def test_word_boundary_is_required_to_avoid_flagging_ordinary_words(monkeypatch) -> None:
    """Отрицательный контроль для теста «...is_not_rejected_as_a_
    substring_elsewhere» выше: без проверки границы слова — наивное
    «пароль начинается с транслитерации токена» — «Lightning7!»
    ОШИБОЧНО отклоняется, хотя правило не должно его трогать."""
    monkeypatch.setattr(
        password_policy,
        "_starts_with_word_boundary",
        lambda password_lower, needle: password_lower.startswith(needle.strip().lower())
        if needle.strip()
        else False,
    )

    with pytest.raises(WeakPasswordError):
        validate_password_strength("Lightning7!", full_name="Ли Иван Петрович")


def test_password_containing_transliterated_full_name_is_rejected() -> None:
    """ГЛАВНОЕ (PR review, ПРОВЕРКА п. 1): сотрудник «Иванов Иван
    Иванович» не может задать пароль «Ivanov2026!» — ФИО хранится
    кириллицей, пароль латиницей, без транслитерации сравнение не
    совпадёт НИ РАЗУ."""
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("Ivanov2026!", full_name="Иванов Иван Иванович")

    assert password_policy.VIOLATION_CONTAINS_IDENTITY in _violation_codes(exc_info.value)


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
    codes = {v.code for v in violations}
    assert password_policy.VIOLATION_TOO_SHORT in codes
    assert password_policy.VIOLATION_INSUFFICIENT_CHARACTER_CLASSES in codes
    assert password_policy.VIOLATION_CONTAINS_IDENTITY in codes


# --- Контекст необязателен ----------------------------------------------------


def test_missing_context_only_skips_the_identity_rule() -> None:
    # Без email/login/full_name правило про личность не применяется —
    # остальные правила (здесь: состав символов) продолжают работать.
    with pytest.raises(WeakPasswordError) as exc_info:
        validate_password_strength("qwertyui")

    assert password_policy.VIOLATION_CONTAINS_IDENTITY not in _violation_codes(exc_info.value)
