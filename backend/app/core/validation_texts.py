"""Русские тексты ошибок валидации запроса (422), `T-M2-28`, `NFR-LOC-001`.

`ui.md` §7 запрещает переписывать серверные формулировки на клиенте, а `NFR-LOC-001` требует
русского интерфейса - поэтому текст `msg` переводится ЗДЕСЬ, на сервере, по машиночитаемому
`type` ошибки pydantic, и эта карта - единственное место русских текстов 422. Таблицы
текстов на фронтенде нет и быть не должно.

Что можно положить в текст (`NFR-SEC-004`, `NFR-SEC-001/002`): только то, что задано СХЕМОЙ -
предел из `ctx` (максимальная длина, нижняя граница, число знаков), и только если он число.
Присланное значение в текст не попадает никогда: оно не берётся ни из `input`, ни из `msg`,
ни из строковых полей `ctx` (в `ctx.error` у ошибок разбора даты лежит диагностика
библиотеки). Единственное исключение - `value_error`: там `msg` = `"Value error, "` + текст
нашего собственного `ValueError` в валидаторе схемы (`schemas/employee.py`, `schemas/balance.py`),
текст пишем мы, литералом; префикс отрезается, а текст без кириллицы заменяется запасным.
Граница (правило 8): это защищает от значения, которое пришло бы из библиотеки; валидатор,
который сам вставит значение в текст своего `ValueError`, эта карта не остановит - его ловит
ревью и тесты `NFR-SEC-004` в `test_main_exception_handlers.py`.

Неизвестный `type` даёт РУССКИЙ запасной текст, а не исходный английский `msg`: новый вид
ошибки не может молча нарушить `NFR-LOC-001`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

FALLBACK_TEXT = "Недопустимое значение."

_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_VALUE_ERROR_PREFIX = "Value error, "

_Builder = Callable[[Mapping[str, Any]], str]


def _number(ctx: Mapping[str, Any], key: str) -> str | None:
    """Предел из `ctx`, только если это число (не строка, не произвольный объект)."""
    value = ctx.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return None
    return str(value)


def _limited_by(key: str) -> Callable[[_Builder], _Builder]:
    """Помечает запись-функцию ключом `ctx`, из которого она берёт предел (`limit_key`): по этой
    метке тест перебирает записи сам, не держа второй список ключей."""

    def mark(build: _Builder) -> _Builder:
        build.limit_key = key  # type: ignore[attr-defined]
        return build

    return mark


def _with_limit(template: str, plain: str, key: str) -> _Builder:
    @_limited_by(key)
    def build(ctx: Mapping[str, Any]) -> str:
        limit = _number(ctx, key)
        return template.format(limit) if limit is not None else plain

    return build


@_limited_by("max_length")
def _too_long(ctx: Mapping[str, Any]) -> str:
    limit = _number(ctx, "max_length")
    # `field_type` - класс поля из схемы ("Value" для строки, "List" для списка), не данные.
    unit = "символов" if ctx.get("field_type") in (None, "Value") else "элементов"
    if limit is None:
        return "Слишком длинное значение."
    return f"Слишком длинное значение: не более {limit} {unit}."


@_limited_by("min_length")
def _too_short(ctx: Mapping[str, Any]) -> str:
    limit = _number(ctx, "min_length")
    unit = "символов" if ctx.get("field_type") in (None, "Value") else "элементов"
    if limit is None:
        return "Слишком короткое значение."
    return f"Слишком короткое значение: не менее {limit} {unit}."


_DATE = "Неверная дата, нужен формат ГГГГ-ММ-ДД."
_INTEGER = "Значение должно быть целым числом."
_NUMBER = "Значение должно быть числом."
_BOOLEAN = "Значение должно быть логическим (да или нет)."
_OBJECT = "Тело запроса должно быть объектом."

_BY_TYPE: dict[str, str | _Builder] = {
    "missing": "Обязательное поле.",
    "string_type": "Значение должно быть строкой.",
    "int_type": _INTEGER,
    "int_parsing": _INTEGER,
    "int_from_float": _INTEGER,
    "float_type": _NUMBER,
    "float_parsing": _NUMBER,
    "decimal_type": _NUMBER,
    "decimal_parsing": _NUMBER,
    "decimal_max_digits": _with_limit(
        "Слишком много цифр: не более {}.", "Слишком много цифр.", "max_digits"
    ),
    "decimal_max_places": _with_limit(
        "Слишком много знаков после запятой: не более {}.",
        "Слишком много знаков после запятой.",
        "decimal_places",
    ),
    "bool_type": _BOOLEAN,
    "bool_parsing": _BOOLEAN,
    "date_type": _DATE,
    "date_parsing": _DATE,
    "date_from_datetime_parsing": _DATE,
    "date_from_datetime_inexact": _DATE,
    "too_long": _too_long,
    "string_too_long": _too_long,
    "too_short": _too_short,
    "string_too_short": _too_short,
    "greater_than_equal": _with_limit(
        "Значение не может быть меньше {}.", "Значение слишком мало.", "ge"
    ),
    "greater_than": _with_limit("Значение должно быть больше {}.", "Значение слишком мало.", "gt"),
    "less_than_equal": _with_limit(
        "Значение не может быть больше {}.", "Значение слишком велико.", "le"
    ),
    "less_than": _with_limit("Значение должно быть меньше {}.", "Значение слишком велико.", "lt"),
    "literal_error": FALLBACK_TEXT,
    "enum": FALLBACK_TEXT,
    "string_pattern_mismatch": "Значение не соответствует допустимому формату.",
    "list_type": "Значение должно быть списком.",
    "dict_type": "Значение должно быть объектом.",
    "model_attributes_type": _OBJECT,
    "model_type": _OBJECT,
    "json_invalid": "Тело запроса не является корректным JSON.",
}


def russian_message(error: Mapping[str, Any]) -> str:
    """Русский текст одной ошибки pydantic (`exc.errors()`): по `type`, без присланного значения."""
    error_type = error.get("type")
    ctx = error.get("ctx")
    ctx = ctx if isinstance(ctx, Mapping) else {}
    if error_type == "value_error":
        # Текст нашего собственного `ValueError` в валидаторе схемы (см. докстринг модуля).
        message = error.get("msg")
        if isinstance(message, str):
            text = message.removeprefix(_VALUE_ERROR_PREFIX)
            if _CYRILLIC.search(text):
                return text
        return FALLBACK_TEXT
    entry = _BY_TYPE.get(error_type) if isinstance(error_type, str) else None
    if entry is None:
        return FALLBACK_TEXT
    return entry(ctx) if callable(entry) else entry
