"""Парольная политика для локальных учётных записей (T-M1-25/T-M1-38,
FR-AUTH-002, NFR-SEC-004).

Значения по умолчанию из требований; настраиваемость через
`business_rules_config` появится в M2 (T-M2-09).

`validate_password_strength` собирает ВСЕ нарушенные требования и
поднимает их одним `WeakPasswordError` (FR-AUTH-002 v1.8: «показывать
все невыполненные требования сразу, а не первое из них») — не
возвращается после первого найденного нарушения.
"""

from __future__ import annotations

import datetime
from pathlib import Path

# Переносятся в business_rules_config при реализации T-M2-09.
DEFAULT_MIN_PASSWORD_LENGTH = 8
DEFAULT_MAX_FAILED_ATTEMPTS = 5
DEFAULT_LOCKOUT_DURATION = datetime.timedelta(minutes=15)

# Токены ФИО короче этого не проверяются: короткие фамилии/инициалы
# («Ли», «О.») иначе блокировали бы огромную часть словаря паролей
# случайными совпадениями, а не защищали от использования имени.
_MIN_NAME_TOKEN_LENGTH = 4

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


def _full_name_tokens(full_name: str) -> list[str]:
    return [tok for tok in full_name.split() if len(tok) >= _MIN_NAME_TOKEN_LENGTH]


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

    class_count = sum(
        [
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
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
        for token in _full_name_tokens(full_name):
            if _contains_case_insensitive(
                password_lower, token
            ) or _contains_case_insensitive(password_lower, _transliterate(token)):
                contains_identity = True
                break
    if contains_identity:
        violations.append("Password must not contain the login, e-mail, or employee's name")

    if password_lower in COMMON_PASSWORDS:
        violations.append("Password is one of the most common passwords and cannot be used")

    if violations:
        raise WeakPasswordError(violations)
