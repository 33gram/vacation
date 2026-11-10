"""Тип события в схеме ответа остатка перечисляет ровно типы журнала (`T-M2-25`).

Без этого откат `entry_type: str` не краснел нигде на бэкенде: клиент TS после перегенерации
получил бы `string`, `Record<EntryType, string>` выродился бы в `Record<string, string>`, и
компилятор перестал бы ловить новый тип без подписи. Сверка идёт с тем же кортежем
`LEAVE_BALANCE_ENTRY_TYPES`, что и `CheckConstraint`, второго списка значений здесь нет.

ГРАНИЦА (правило 8): тест сверяет схему ответа, а не сгенерированный `schema.d.ts` - его
совпадение со схемой охраняет проверка CI «Generated TS client matches backend schema».
"""

from app.models.leave_balance_entry import LEAVE_BALANCE_ENTRY_TYPES
from app.schemas.balance import BalanceEntryOut


def test_entry_type_in_the_response_schema_lists_exactly_the_journal_types() -> None:
    prop = BalanceEntryOut.model_json_schema()["properties"]["entry_type"]

    assert "enum" in prop, f"entry_type в схеме не перечисление: {prop}"
    assert list(prop["enum"]) == list(LEAVE_BALANCE_ENTRY_TYPES)
