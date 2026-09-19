"""Разбор файлов импорта сотрудников (T-M1-11, FR-EMP-003).

Не знает про БД — задача этого модуля: превратить файл (CSV/XLSX) в
список строк с их номером в файле. Проверка значений полей, поиск
подразделения/руководителя и запись — в `import_service.py`.
"""

from __future__ import annotations

import csv
import datetime
import io
from dataclasses import dataclass

from openpyxl import load_workbook

# Колонки файла: обязательные поля карточки по FR-EMP-001 (ФИО, табельный
# номер, подразделение, должность, дата приёма, e-mail, график работы),
# плюс руководитель — по табельному номеру, а не e-mail/id (устойчив к
# смене фамилии, см. plan.md §6) — и leave_category (A-3.1, допускается
# пустым). Обе последние КОЛОНКИ обязаны присутствовать в заголовке;
# пустые ЗНАЧЕНИЯ в них — не ошибка (руководитель пуст только для вершины
# иерархии, leave_category не заполняется на MVP).
EXPECTED_COLUMNS = (
    "full_name",
    "personnel_number",
    "department",
    "position",
    "hire_date",
    "email",
    "work_schedule_type",
    "manager_personnel_number",
    "leave_category",
)

# Жёсткие лимиты (NFR-SCALE-001: штат до 750 человек — запас с кратным
# отрывом, не больше). Заодно снимают вопрос о zip-бомбах в XLSX: сколь
# угодно сжатый файл всё равно должен пройти оба лимита на выходе, а
# `read_only=True` не разворачивает лист в память целиком.
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MiB
MAX_ROWS = 1000


class ImportFileError(Exception):
    """Файл целиком не может быть обработан (не тот формат, файл
    превышает лимит, отсутствует обязательная колонка) — в отличие от
    построчных ошибок (`import_service.RowError`), это не про
    какую-то одну строку."""


@dataclass(frozen=True)
class ParsedRow:
    row_number: int  # Как в файле: 1 — заголовок, 2 — первая строка данных.
    values: dict[str, str | None]


def parse_import_file(filename: str, content: bytes) -> list[ParsedRow]:
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise ImportFileError(f"File exceeds the {MAX_FILE_SIZE_BYTES} byte limit")

    lower_name = filename.lower()
    if lower_name.endswith(".csv"):
        rows = _parse_csv(content)
    elif lower_name.endswith(".xlsx"):
        rows = _parse_xlsx(content)
    else:
        raise ImportFileError("Unsupported file type: expected .csv or .xlsx")

    if len(rows) > MAX_ROWS:
        raise ImportFileError(f"File exceeds the {MAX_ROWS} data row limit")

    return rows


def _require_expected_columns(header: list[str]) -> None:
    missing = [column for column in EXPECTED_COLUMNS if column not in header]
    if missing:
        raise ImportFileError(f"Missing required column(s): {', '.join(missing)}")


def _row_values(header: list[str], raw_row: list[str | None]) -> dict[str, str | None]:
    return {
        column: raw_row[i] if i < len(raw_row) else None
        for i, column in enumerate(header)
    }


def _is_blank_row(values: dict[str, str | None]) -> bool:
    return all(v is None for v in values.values())


def _parse_csv(content: bytes) -> list[ParsedRow]:
    text = content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ImportFileError("File is empty") from None
    _require_expected_columns(header)

    rows = []
    for row_number, raw_row in enumerate(reader, start=2):
        cells = [cell.strip() or None for cell in raw_row]
        values = _row_values(header, cells)
        if _is_blank_row(values):
            continue
        rows.append(ParsedRow(row_number=row_number, values=values))
    return rows


def _xlsx_cell_to_str(cell: object) -> str | None:
    if cell is None:
        return None
    if isinstance(cell, str):
        return cell.strip() or None
    if isinstance(cell, datetime.datetime):
        return cell.date().isoformat()
    if isinstance(cell, datetime.date):
        return cell.isoformat()
    return str(cell).strip() or None


def _parse_xlsx(content: bytes) -> list[ParsedRow]:
    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        sheet = workbook.active
        rows_iter = sheet.iter_rows(values_only=True)
        try:
            header_row = next(rows_iter)
        except StopIteration:
            raise ImportFileError("File is empty") from None
        header = [(str(c).strip() if c is not None else "") for c in header_row]
        _require_expected_columns(header)

        rows = []
        for row_number, raw_row in enumerate(rows_iter, start=2):
            cells = [_xlsx_cell_to_str(c) for c in raw_row]
            values = _row_values(header, cells)
            if _is_blank_row(values):
                continue
            rows.append(ParsedRow(row_number=row_number, values=values))
        return rows
    finally:
        workbook.close()
