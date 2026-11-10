"""Перечисления в схеме ответа остатка совпадают с кортежами-источниками (`T-M2-25`, `T-M2-29`).

Без этого откат `entry_type: str` или `unit: str` не краснел нигде на бэкенде: клиент TS после
перегенерации получил бы `string`, `Record<EntryType, string>` / `Record<Unit, string>`
выродились бы в `Record<string, string>`, и компилятор перестал бы ловить новое значение без
подписи. Сверка идёт с теми же кортежами, что строят `CheckConstraint` таблиц
(`LEAVE_BALANCE_ENTRY_TYPES`, `LEAVE_TYPE_UNIT_VALUES`), второго списка значений здесь нет.

ГРАНИЦА (правило 8): тест сверяет схему ответа, а не сгенерированный `schema.d.ts` - его
совпадение со схемой охраняет проверка CI «Generated TS client matches backend schema».
"""

from app.models.leave_balance_entry import LEAVE_BALANCE_ENTRY_TYPES
from app.models.leave_type import LEAVE_TYPE_UNIT_VALUES
from app.schemas.balance import BalanceEntryOut, LeaveTypeBalanceOut


def test_entry_type_in_the_response_schema_lists_exactly_the_journal_types() -> None:
    prop = BalanceEntryOut.model_json_schema()["properties"]["entry_type"]

    assert "enum" in prop, f"entry_type в схеме не перечисление: {prop}"
    assert list(prop["enum"]) == list(LEAVE_BALANCE_ENTRY_TYPES)


def test_unit_in_the_response_schema_lists_exactly_the_leave_type_units() -> None:
    prop = LeaveTypeBalanceOut.model_json_schema()["properties"]["unit"]

    assert "enum" in prop, f"unit в схеме не перечисление: {prop}"
    assert list(prop["enum"]) == list(LEAVE_TYPE_UNIT_VALUES)
