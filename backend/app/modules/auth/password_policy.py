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
from dataclasses import dataclass
from pathlib import Path

from app.core.security import BCRYPT_MAX_INPUT_BYTES, exceeds_bcrypt_input_limit

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
#
# PR #140 review (четвёртый круг): значение и предикат теперь живут в
# `core.security` (`BCRYPT_MAX_INPUT_BYTES`/`exceeds_bcrypt_input_limit`)
# — это свойство bcrypt, а не парольной политики. Импорт здесь оставляет
# имя `MAX_PASSWORD_BYTES` на месте, чтобы остальные файлы и тесты этого
# модуля не меняли свои импорты.
MAX_PASSWORD_BYTES = BCRYPT_MAX_INPUT_BYTES

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

# T-M1-45: коды нарушений парольной политики, по одному на критерий
# FR-AUTH-002 — стабильные идентификаторы для сопоставления с пунктом
# чек-листа в интерфейсе (NFR-LOC-001 требует русский интерфейс, но код
# не должен меняться при переформулировке текста). Порядок — тот же,
# что и порядок проверок в `validate_password_strength` ниже.
VIOLATION_TOO_SHORT = "too_short"
VIOLATION_EXCEEDS_BYTE_LIMIT = "exceeds_byte_limit"
VIOLATION_CONTAINS_WHITESPACE = "contains_whitespace"
VIOLATION_CONTAINS_INVISIBLE_OR_CONTROL_CHARACTERS = "contains_invisible_or_control_characters"
VIOLATION_INSUFFICIENT_CHARACTER_CLASSES = "insufficient_character_classes"
VIOLATION_CONTAINS_IDENTITY = "contains_identity"
VIOLATION_COMMON_PASSWORD = "common_password"

PASSWORD_POLICY_VIOLATION_CODES = (
    VIOLATION_TOO_SHORT,
    VIOLATION_EXCEEDS_BYTE_LIMIT,
    VIOLATION_CONTAINS_WHITESPACE,
    VIOLATION_CONTAINS_INVISIBLE_OR_CONTROL_CHARACTERS,
    VIOLATION_INSUFFICIENT_CHARACTER_CLASSES,
    VIOLATION_CONTAINS_IDENTITY,
    VIOLATION_COMMON_PASSWORD,
)


@dataclass(frozen=True)
class PasswordPolicyViolation:
    """Код + русская формулировка одного нарушения (T-M1-45). Один и тот
    же объект используется и как элемент `WeakPasswordError.violations`
    (что не так с ЭТИМ паролем), и как элемент `get_password_policy_
    hints()` (что вообще проверяется, безотносительно конкретного
    пароля) — текст в обоих случаях описывает само правило, а не что-то
    специфичное для введённого значения, поэтому формулировка совпадает
    дословно."""

    code: str
    message: str


def _password_policy_messages(*, min_length: int) -> dict[str, str]:
    """ЕДИНСТВЕННЫЙ источник русских формулировок парольной политики —
    и для сообщений о нарушении, и для подсказок `GET /api/auth/
    password-policy` (T-M1-45): один код существует в ровно одном
    месте этого словаря, поэтому набор кодов, которые может поднять
    `validate_password_strength`, и набор кодов, которые перечисляет
    эндпоинт-подсказка, не могут разойтись сами по себе (см. тест
    `test_password_policy_codes_and_hints_are_the_same_set`). Тексты —
    по формулировкам FR-AUTH-002 (`requirements.md`), не придуманы
    заново; минимальная длина — параметр функции, а не хардкод: это
    бизнес-правило, которое в M2 (`T-M2-09`) переезжает в
    `business_rules_config`."""
    return {
        VIOLATION_TOO_SHORT: f"Пароль должен быть не короче {min_length} символов.",
        VIOLATION_EXCEEDS_BYTE_LIMIT: (
            f"Пароль не должен превышать {MAX_PASSWORD_BYTES} байт в кодировке "
            "UTF-8 — алгоритм хэширования учитывает только первые 72 байта пароля; "
            "для кириллицы граница составляет около 36 символов, а не 72."
        ),
        VIOLATION_CONTAINS_WHITESPACE: (
            "Пароль не должен содержать пробельные символы (пробел, табуляция, "
            "перевод строки, неразрывный пробел)."
        ),
        VIOLATION_CONTAINS_INVISIBLE_OR_CONTROL_CHARACTERS: (
            "Пароль не должен содержать невидимые, управляющие или форматирующие "
            "символы Unicode (например, символы нулевой ширины ZWJ/ZWNJ, мягкий "
            "перенос)."
        ),
        VIOLATION_INSUFFICIENT_CHARACTER_CLASSES: (
            "Пароль должен содержать символы минимум трёх типов из четырёх: "
            "строчные буквы, заглавные буквы, цифры, специальные символы."
        ),
        VIOLATION_CONTAINS_IDENTITY: (
            "Пароль не должен содержать логин, e-mail или ФИО сотрудника."
        ),
        VIOLATION_COMMON_PASSWORD: (
            "Пароль входит в список распространённых или ранее скомпрометированных "
            "паролей."
        ),
    }


def get_password_policy_hints(
    *, min_length: int = DEFAULT_MIN_PASSWORD_LENGTH
) -> list[PasswordPolicyViolation]:
    """`GET /api/auth/password-policy` (T-M1-45): состав действующих
    правил с уже подставленными параметрами на русском — чтобы
    интерфейс не хардкодил ни тексты, ни значения (например, `min_
    length`). Возвращает ВСЕ коды из `PASSWORD_POLICY_VIOLATION_CODES`
    без исключения — порядок соответствует порядку проверок в
    `validate_password_strength`."""
    messages = _password_policy_messages(min_length=min_length)
    return [
        PasswordPolicyViolation(code=code, message=messages[code])
        for code in PASSWORD_POLICY_VIOLATION_CODES
    ]


class WeakPasswordError(Exception):
    """Пароль не прошёл один или несколько критериев политики.

    Несёт СПИСОК всех нарушенных требований (`violations`), а не одно
    сообщение, — этого требует FR-AUTH-002 v1.8. Каждый элемент —
    `PasswordPolicyViolation` (T-M1-45): код + русская формулировка
    (NFR-LOC-001), а не голая строка — интерфейс сопоставляет нарушение
    с пунктом чек-листа по коду, не по тексту. `str(exc)` остаётся
    удобочитаемой строкой (сообщения через «; ») для мест, которым
    структура не нужна; вызывающий код, которому нужен именно список
    (ответ 400 с перечислением всех нарушений), берёт `exc.violations`
    напрямую."""

    def __init__(self, violations: list[PasswordPolicyViolation]) -> None:
        self.violations = violations
        super().__init__("; ".join(v.message for v in violations))


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
    violations: list[PasswordPolicyViolation] = []
    password_lower = password.lower()
    # T-M1-45: те же формулировки, что отдаёт `GET /api/auth/password-
    # policy` (см. `get_password_policy_hints`) — один словарь на оба
    # потребителя, чтобы код и текст нарушения не могли разойтись с
    # тем, что показано в подсказке.
    messages = _password_policy_messages(min_length=min_length)

    if len(password) < min_length:
        violations.append(
            PasswordPolicyViolation(VIOLATION_TOO_SHORT, messages[VIOLATION_TOO_SHORT])
        )

    if exceeds_bcrypt_input_limit(password):
        violations.append(
            PasswordPolicyViolation(
                VIOLATION_EXCEEDS_BYTE_LIMIT, messages[VIOLATION_EXCEEDS_BYTE_LIMIT]
            )
        )

    # PR #140 review: пробел (в т.ч. неразрывный — прилетает при
    # копировании из офисных редакторов) запрещён отдельным критерием,
    # а не молча учитывается как «специальный символ» в составе ниже —
    # `str.isspace()` покрывает пробел, табуляцию, перевод строки и
    # неразрывный пробел (U+00A0) одним вызовом.
    if any(c.isspace() for c in password):
        violations.append(
            PasswordPolicyViolation(
                VIOLATION_CONTAINS_WHITESPACE, messages[VIOLATION_CONTAINS_WHITESPACE]
            )
        )

    # PR #140 review (второй круг): невидимые/управляющие Unicode-символы
    # (ZWJ, мягкий перенос, BEL, ...) запрещены отдельным критерием, тем
    # же обоснованием, что и пробел выше, — пользователь не может
    # сознательно повторить то, чего не видит.
    if any(_is_invisible_control_character(c) for c in password):
        violations.append(
            PasswordPolicyViolation(
                VIOLATION_CONTAINS_INVISIBLE_OR_CONTROL_CHARACTERS,
                messages[VIOLATION_CONTAINS_INVISIBLE_OR_CONTROL_CHARACTERS],
            )
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
            PasswordPolicyViolation(
                VIOLATION_INSUFFICIENT_CHARACTER_CLASSES,
                messages[VIOLATION_INSUFFICIENT_CHARACTER_CLASSES],
            )
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
        violations.append(
            PasswordPolicyViolation(
                VIOLATION_CONTAINS_IDENTITY, messages[VIOLATION_CONTAINS_IDENTITY]
            )
        )

    if password_lower in COMMON_PASSWORDS:
        violations.append(
            PasswordPolicyViolation(VIOLATION_COMMON_PASSWORD, messages[VIOLATION_COMMON_PASSWORD])
        )

    if violations:
        raise WeakPasswordError(violations)
