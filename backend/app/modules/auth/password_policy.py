"""Парольная политика для локальных учётных записей (T-M1-25/T-M1-38,
FR-AUTH-002, NFR-SEC-004).

Значения по умолчанию из требований; настраиваемость через
`business_rules_config` появится в M2 (T-M2-09).

`validate_password_strength` собирает ВСЕ нарушенные требования и
поднимает их одним `WeakPasswordError` (FR-AUTH-002 v1.8: «показывать
все невыполненные требования сразу, а не первое из них») — не
возвращается после первого найденного нарушения.

Верхняя граница длины (FR-AUTH-002 v1.9, `MAX_PASSWORD_BYTES`) не
косметическая: bcrypt хэширует только первые 72 БАЙТА пароля, дальше
молча обрезает — без этой проверки два разных пароля с общим
72-байтовым началом открывали бы одну и ту же учётную запись (см.
докстринг `MAX_PASSWORD_BYTES`).
"""

from __future__ import annotations

import datetime
import unicodedata
from pathlib import Path

# Переносятся в business_rules_config при реализации T-M2-09.
DEFAULT_MIN_PASSWORD_LENGTH = 8
DEFAULT_MAX_FAILED_ATTEMPTS = 5
DEFAULT_LOCKOUT_DURATION = datetime.timedelta(minutes=15)

# PR #140 review: bcrypt (`core/security.py`, NFR-SEC-004) hashes only
# the first 72 BYTES of its input — a longer password is silently
# truncated there, not rejected, so two different passwords sharing a
# 72-byte UTF-8 prefix open the same account. Confirmed live before
# this fix: a 5003-character password, its 72-byte prefix, and that
# prefix with a different tail all satisfied `verify_password` against
# the same hash. Checked in BYTES, not characters (`len(password) <
# min_length` above is correct in characters for the LOWER bound, but
# wrong here — the 72-byte ceiling is about bcrypt's input size, and a
# character is 1-4 UTF-8 bytes depending on script; ~36 characters for
# Cyrillic, not 72).
MAX_PASSWORD_BYTES = 72

# pydantic `max_length` on password fields in `schemas/*.py` — a coarse
# guard so a request with a multi-megabyte password is rejected by
# validation before it ever reaches hashing, not the authoritative
# 72-byte rule itself (that's `MAX_PASSWORD_BYTES`, checked in bytes,
# above). Deliberately well above 72: a tight character bound would
# risk rejecting a byte-valid multi-byte-script password before the
# byte check even runs.
MAX_PASSWORD_LENGTH_CHARS = 128

# PR #140 review: части ФИО от этой длины запрещены В ЛЮБОМ МЕСТЕ
# пароля — как подстрока где угодно; короче — см.
# `_SHORT_NAME_TOKEN_MIN_LENGTH` ниже (только начало пароля, не любое
# место: как подстрока где угодно они заблокировали бы огромную долю
# обычных паролей случайными совпадениями).
_LONG_NAME_TOKEN_LENGTH = 4

# Токены длиной 2–3 символа («Ли», «Пак», «Ким») запрещены только КАК
# НАЧАЛО пароля (см. `_starts_with_word_boundary`) — это ловит именно
# распространённый слабый пароль вида «короткая фамилия + цифры», не
# трогая обычные слова, которые случайно начинаются с тех же букв.
# Токены короче этого (одна буква — инициал вида «О.») не проверяются
# вовсе: с ними совпадений было бы слишком много, а сигнала — почти
# нет.
_SHORT_NAME_TOKEN_MIN_LENGTH = 2

_COMMON_PASSWORDS_PATH = Path(__file__).resolve().parent / "common_passwords.txt"

# Простая, побуквенная транслитерация — ФИО в БД хранится кириллицей
# (FR-EMP-001), а локальный пароль почти всегда набирается латиницей;
# без нормализации в одну письменность правило «пароль не должен
# содержать часть ФИО» не сработает НИ РАЗУ для реального пароля вида
# «Ivanov2026!» при ФИО «Иванов Иван Иванович» — сравнение шло бы
# «ivanov2026!» против «иванов», разных алфавитов, без единого общего
# символа. Таблица — по звучанию (как сотрудник сам обычно
# транслитерирует свою фамилию), не ГОСТ/ISO паспортная норма: цель —
# поймать пароль, который человек реально может выбрать, а не пройти
# формальный стандарт транслитерации.
_CYRILLIC_TO_LATIN: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _transliterate(text: str) -> str:
    return "".join(_CYRILLIC_TO_LATIN.get(ch, ch) for ch in text.lower())


def _load_common_passwords(path: Path) -> frozenset[str]:
    """Один раз при импорте модуля — не на каждый вызов `validate_
    password_strength`. Пустые строки и строки-комментарии (`#...`) в
    начале файла пропускаются; сравнение без учёта регистра, поэтому
    каждая запись приводится к нижнему регистру здесь же, один раз, а
    не при каждой проверке."""
    entries: set[str] = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            entries.add(stripped.lower())
    return frozenset(entries)


COMMON_PASSWORDS = _load_common_passwords(_COMMON_PASSWORDS_PATH)


class WeakPasswordError(Exception):
    """Пароль не прошёл один или несколько критериев политики.

    Несёт СПИСОК всех нарушенных требований (`violations`), а не одно
    сообщение, — этого требует FR-AUTH-002 v1.8. `str(exc)` остаётся
    удобочитаемой строкой (нарушения через «; ») для мест, которым
    структура не нужна; вызывающий код, которому нужен именно список
    (ответ 400 с перечислением всех нарушений), берёт `exc.violations`
    напрямую."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__("; ".join(violations))


def _contains_case_insensitive(password_lower: str, needle: str) -> bool:
    needle = needle.strip().lower()
    return bool(needle) and needle in password_lower


def _starts_with_word_boundary(password_lower: str, needle: str) -> bool:
    """`password_lower` начинается с `needle`, и сразу за ним — конец
    пароля или НЕ буква (PR #140 review). Без этого условия короткий
    токен вроде «ли» (→ транслитерация «li») ловил бы не только пароли
    вида «Ли123456!» (после «li» — цифра, граница есть, это и нужно
    поймать), но и обычные слова вроде «Lightning7!» («li» — начало
    слова «lightning», после «li» идёт «g», это НЕ совпадение с
    фамилией, а совпадение первых двух букв случайного слова)."""
    needle = needle.strip().lower()
    if not needle or not password_lower.startswith(needle):
        return False
    if len(password_lower) == len(needle):
        return True
    return not password_lower[len(needle)].isalpha()


def _full_name_tokens(full_name: str) -> list[str]:
    """Части ФИО от `_LONG_NAME_TOKEN_LENGTH` символов — проверяются
    как подстрока в любом месте пароля. Контракт `full_name` (`plan.md`
    §4): хранится одной строкой, части — по пробельным символам, тем же
    `str.split()`, что и здесь."""
    return [tok for tok in full_name.split() if len(tok) >= _LONG_NAME_TOKEN_LENGTH]


def _full_name_short_tokens(full_name: str) -> list[str]:
    """Части ФИО длиной 2–3 символа — проверяются только как начало
    пароля, см. `_starts_with_word_boundary`."""
    return [
        tok
        for tok in full_name.split()
        if _SHORT_NAME_TOKEN_MIN_LENGTH <= len(tok) < _LONG_NAME_TOKEN_LENGTH
    ]


def _is_invisible_control_character(c: str) -> bool:
    """Unicode-категория C* (Cc управляющие, Cf форматирующие вроде ZWJ/
    ZWNJ и мягкого переноса, Cn неприсвоенные, ...) — PR #140 review,
    второй круг: пользователь не может сознательно повторить символ,
    который не видит. Пробельные символы (тоже частично попадают в Cc,
    например табуляция) сюда не относятся — для них уже есть свой
    критерий выше, с собственным сообщением; здесь `not c.isspace()`
    исключает двойное срабатывание на одном и том же символе."""
    return not c.isspace() and unicodedata.category(c).startswith("C")


def _is_combining_mark(c: str) -> bool:
    """Unicode-категория M* (комбинирующие знаки, например «́» U+0301) —
    обычная часть письменности многих языков, сама по себе не
    отклоняется (см. `_is_invisible_control_character` для того, что
    отклоняется). Используется только чтобы НЕ засчитывать такой символ
    за специальный в `validate_password_strength` — иначе стойкость
    пароля держалась бы на знаке, невидимом при повторном наборе."""
    return unicodedata.category(c).startswith("M")


def validate_password_strength(
    password: str,
    *,
    min_length: int = DEFAULT_MIN_PASSWORD_LENGTH,
    login: str | None = None,
    email: str | None = None,
    full_name: str | None = None,
) -> None:
    """Контекст (`login`/`email`/`full_name`) — необязательные именованные
    параметры: существующие вызовы без контекста продолжают работать
    (пропускают только правило «не содержит логин/e-mail/ФИО»), а
    вызывающий код, у которого контекста НЕТ (например, служебная
    учётная запись без карточки сотрудника — см. `bootstrap.py`),
    передаёт `None` явно, а не полагается на то, что правило "как-то
    само" не сработает."""
    violations: list[str] = []
    password_lower = password.lower()

    if len(password) < min_length:
        violations.append(f"Password must be at least {min_length} characters long")

    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        violations.append(
            f"Password must not exceed {MAX_PASSWORD_BYTES} bytes when UTF-8 encoded "
            "(bcrypt only hashes the first 72 bytes of a password; for Cyrillic text "
            "this is roughly 36 characters, not 72)"
        )

    # PR #140 review: пробел (в т.ч. неразрывный — прилетает при
    # копировании из офисных редакторов) запрещён отдельным критерием,
    # а не молча учитывается как «специальный символ» в составе ниже —
    # `str.isspace()` покрывает пробел, табуляцию, перевод строки и
    # неразрывный пробел (U+00A0) одним вызовом.
    if any(c.isspace() for c in password):
        violations.append("Password must not contain whitespace characters")

    # PR #140 review (второй круг): невидимые/управляющие Unicode-символы
    # (ZWJ, мягкий перенос, BEL, ...) запрещены отдельным критерием, тем
    # же обоснованием, что и пробел выше, — пользователь не может
    # сознательно повторить то, чего не видит.
    if any(_is_invisible_control_character(c) for c in password):
        violations.append(
            "Password must not contain invisible or control characters "
            "(e.g. zero-width joiners, soft hyphens, control codes)"
        )

    # Специальный символ определяется через отрицание («не буква, не
    # цифра, не пробел»), а не перечнем допустимых символов (PR #140
    # review): перечень пришлось бы показывать в подсказке интерфейса,
    # и он лишал бы статуса спецсимвола всё, что в него не попало
    # («№», «±», «€», …). Без вычитания пробела из этого класса пароль
    # вида "abcdefg1 " (строчные буквы, цифра, завершающий пробел)
    # набирал бы три класса из четырёх, не содержа ни одного НАСТОЯЩЕГО
    # спецсимвола — пробел уже наказан отдельным критерием выше, здесь
    # он просто не в счёт. То же — невидимые/управляющие символы (уже
    # отклонены отдельно выше) и комбинирующие знаки (не отклоняются,
    # но статуса спецсимвола тоже не дают, см. `_is_combining_mark`).
    class_count = sum(
        [
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(
                not c.isalnum()
                and not c.isspace()
                and not _is_invisible_control_character(c)
                and not _is_combining_mark(c)
                for c in password
            ),
        ]
    )
    if class_count < 3:
        violations.append(
            "Password must contain at least three of: lowercase letters, "
            "uppercase letters, digits, special characters"
        )

    contains_identity = False
    if login and _contains_case_insensitive(password_lower, login):
        contains_identity = True
    if email:
        local_part = email.split("@", 1)[0]
        if _contains_case_insensitive(password_lower, local_part):
            contains_identity = True
    if full_name:
        # Длинные части (>= _LONG_NAME_TOKEN_LENGTH) — где угодно в
        # пароле; короткие (2–3 символа) — только как начало пароля с
        # границей слова (PR #140 review, см. `_starts_with_word_
        # boundary`). Обе проверки — и как есть, и транслитерацией.
        for token in _full_name_tokens(full_name):
            if _contains_case_insensitive(
                password_lower, token
            ) or _contains_case_insensitive(password_lower, _transliterate(token)):
                contains_identity = True
                break
        if not contains_identity:
            for short_token in _full_name_short_tokens(full_name):
                if _starts_with_word_boundary(
                    password_lower, short_token
                ) or _starts_with_word_boundary(password_lower, _transliterate(short_token)):
                    contains_identity = True
                    break
    if contains_identity:
        violations.append("Password must not contain the login, e-mail, or employee's name")

    if password_lower in COMMON_PASSWORDS:
        violations.append("Password is one of the most common passwords and cannot be used")

    if violations:
        raise WeakPasswordError(violations)
