"""Карта русских текстов 422 (`app/core/validation_texts.py`, `T-M2-28`, `NFR-LOC-001`) напрямую.

Что проверяется: известный `type` даёт СВОЙ русский текст, неизвестный - русский запасной (а не
английский `msg` библиотеки), предел берётся из `ctx` только числом, присланное значение в текст
не попадает. Ошибки реальных маршрутов - `test_validation_errors_are_russian.py`.
"""

from __future__ import annotations

import re

import pytest
from pydantic import BaseModel, Field, ValidationError

from app.core.validation_texts import _BY_TYPE, FALLBACK_TEXT, russian_message

_CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def test_every_entry_of_the_map_and_the_fallback_are_russian() -> None:
    assert _BY_TYPE, "карта пуста"
    assert _CYRILLIC.search(FALLBACK_TEXT)
    for error_type, entry in _BY_TYPE.items():
        text = entry({}) if callable(entry) else entry
        assert _CYRILLIC.search(text), f"type={error_type}: {text!r}"


def _limit_entries() -> list[tuple[str, object, str]]:
    """Записи карты, зависящие от `ctx`: (type, запись, ключ предела). Ключ берётся из самой
    записи (`limit_key`), второго списка ключей здесь нет; запись-функция без метки - ошибка."""
    found = []
    for error_type, entry in _BY_TYPE.items():
        if not callable(entry):
            continue
        key = getattr(entry, "limit_key", None)
        assert key, f"type={error_type}: у записи-функции нет метки limit_key"
        found.append((error_type, entry, key))
    return found


def test_the_sweep_over_the_map_finds_its_entries() -> None:
    assert len(_limit_entries()) >= 8, "перебор пределов ослеп"


@pytest.mark.parametrize("error_type", [t for t, _, _ in _limit_entries()])
def test_every_limit_entry_puts_a_numeric_limit_into_the_text_and_never_a_string(
    error_type: str,
) -> None:
    [(_, entry, key)] = [e for e in _limit_entries() if e[0] == error_type]
    number = 7654321
    marker = "MARKER-CTX-STRING"

    assert str(number) in entry({key: number}), f"type={error_type}: предел {key} не в тексте"
    assert marker not in entry({key: marker}), f"type={error_type}: строка {key} попала в текст"
    assert str(number) not in entry({}), f"type={error_type}: предел взялся из ничего"


def test_a_known_type_gets_its_own_russian_text() -> None:
    assert russian_message({"type": "missing", "msg": "Field required"}) == "Обязательное поле."


def test_an_unknown_type_gets_the_russian_fallback_not_the_english_msg() -> None:
    text = russian_message({"type": "some_future_pydantic_type", "msg": "Brand new English text"})

    assert text == FALLBACK_TEXT
    assert _CYRILLIC.search(text)
    assert "English" not in text


def test_a_missing_type_gets_the_russian_fallback() -> None:
    assert russian_message({"msg": "English only"}) == FALLBACK_TEXT


def test_the_limit_comes_from_ctx_when_it_is_a_number() -> None:
    error = {"type": "too_long", "ctx": {"max_length": 128, "field_type": "Value"}}

    assert russian_message(error) == "Слишком длинное значение: не более 128 символов."


def test_a_list_limit_is_counted_in_elements() -> None:
    error = {"type": "too_long", "ctx": {"max_length": 3, "field_type": "List"}}

    assert russian_message(error) == "Слишком длинное значение: не более 3 элементов."


def test_a_non_numeric_limit_is_never_put_into_the_text() -> None:
    # Строка в `ctx` может оказаться чем угодно, в том числе присланным значением.
    marker = "MARKER-CTX-STRING"
    text = russian_message({"type": "too_long", "ctx": {"max_length": marker}})

    assert marker not in text
    assert text == "Слишком длинное значение."


def test_the_sent_value_in_input_and_in_msg_is_never_used() -> None:
    marker = "MARKER-SENT-VALUE"
    error = {
        "type": "string_type",
        "msg": f"Input should be a valid string: {marker}",
        "input": marker,
        "ctx": {"error": marker},
    }

    assert marker not in russian_message(error)


def test_value_error_keeps_our_own_russian_text_without_the_library_prefix() -> None:
    error = {
        "type": "value_error",
        "msg": "Value error, Дата корректировки не может быть в будущем.",
    }

    assert russian_message(error) == "Дата корректировки не может быть в будущем."


def test_value_error_without_cyrillic_gets_the_fallback() -> None:
    error = {"type": "value_error", "msg": "Value error, initial_password is required"}

    assert russian_message(error) == FALLBACK_TEXT


class _Probe(BaseModel):
    name: str = Field(max_length=5)
    count: int = Field(ge=1)


@pytest.mark.parametrize(
    "payload",
    [{}, {"name": 1, "count": "x"}, {"name": "toolong", "count": 0}, {"name": "ok", "count": 1.5}],
)
def test_real_pydantic_errors_get_a_russian_non_fallback_text(payload) -> None:
    # Реальные ошибки, не собранные руками: каждый `type`, который pydantic отдаёт на этих
    # телах, обязан быть в карте (иначе тут появился бы запасной текст).
    with pytest.raises(ValidationError) as caught:
        _Probe.model_validate(payload)

    for error in caught.value.errors():
        text = russian_message(error)
        assert _CYRILLIC.search(text), error["type"]
        assert text != FALLBACK_TEXT, f"нет собственного текста у type={error['type']}"
