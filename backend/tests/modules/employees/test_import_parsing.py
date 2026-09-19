"""Разбор файлов импорта (T-M1-11, FR-EMP-003) — без обращения к БД."""

from __future__ import annotations

import datetime
import io

import pytest
from openpyxl import Workbook

from app.modules.employees.import_parsing import (
    EXPECTED_COLUMNS,
    MAX_FILE_SIZE_BYTES,
    MAX_ROWS,
    ImportFileError,
    parse_import_file,
)

_HEADER_LINE = ",".join(EXPECTED_COLUMNS)


def _csv_bytes(*data_lines: str) -> bytes:
    return ("\n".join([_HEADER_LINE, *data_lines])).encode("utf-8")


def test_parse_csv_basic_rows() -> None:
    content = _csv_bytes(
        "Иван Иванов,P-1,IT,Инженер,2024-01-15,ivanov@company.local,standard,,",
        "Пётр Петров,P-2,IT,Инженер,2024-02-01,petrov@company.local,standard,P-1,",
    )

    rows = parse_import_file("employees.csv", content)

    assert [r.row_number for r in rows] == [2, 3]
    assert rows[0].values["full_name"] == "Иван Иванов"
    assert rows[0].values["manager_personnel_number"] is None
    assert rows[1].values["manager_personnel_number"] == "P-1"


def test_parse_csv_skips_fully_blank_rows() -> None:
    content = _csv_bytes(
        "Иван Иванов,P-1,IT,Инженер,2024-01-15,ivanov@company.local,standard,,",
        ",,,,,,,,",
        "Пётр Петров,P-2,IT,Инженер,2024-02-01,petrov@company.local,standard,,",
    )

    rows = parse_import_file("employees.csv", content)

    assert [r.row_number for r in rows] == [2, 4]


def test_parse_csv_missing_required_column_rejects_whole_file() -> None:
    header = ",".join(c for c in EXPECTED_COLUMNS if c != "email")
    content = (header + "\nИван Иванов,P-1,IT,Инженер,2024-01-15,standard,,\n").encode()

    with pytest.raises(ImportFileError, match="email"):
        parse_import_file("employees.csv", content)


def test_parse_csv_empty_file_is_rejected() -> None:
    with pytest.raises(ImportFileError):
        parse_import_file("employees.csv", b"")


def test_parse_rejects_unsupported_extension() -> None:
    with pytest.raises(ImportFileError):
        parse_import_file("employees.txt", _csv_bytes())


def test_parse_rejects_file_over_size_limit() -> None:
    content = b"x" * (MAX_FILE_SIZE_BYTES + 1)

    with pytest.raises(ImportFileError, match="byte limit"):
        parse_import_file("employees.csv", content)


def test_parse_rejects_file_over_row_limit() -> None:
    lines = [
        f"Employee {i},P-{i},IT,Инженер,2024-01-15,e{i}@company.local,standard,,"
        for i in range(MAX_ROWS + 1)
    ]
    content = _csv_bytes(*lines)

    with pytest.raises(ImportFileError, match="row limit"):
        parse_import_file("employees.csv", content)


def _xlsx_bytes(*data_rows: tuple[object, ...]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(list(EXPECTED_COLUMNS))
    for row in data_rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_parse_xlsx_basic_rows_with_date_cell() -> None:
    content = _xlsx_bytes(
        (
            "Иван Иванов",
            "P-1",
            "IT",
            "Инженер",
            datetime.date(2024, 1, 15),
            "ivanov@company.local",
            "standard",
            None,
            None,
        ),
    )

    rows = parse_import_file("employees.xlsx", content)

    assert len(rows) == 1
    assert rows[0].row_number == 2
    assert rows[0].values["hire_date"] == "2024-01-15"


def test_parse_xlsx_missing_required_column_rejects_whole_file() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append([c for c in EXPECTED_COLUMNS if c != "department"])
    sheet.append(["Иван Иванов", "P-1", "Инженер", "2024-01-15", "i@company.local", "standard"])
    buffer = io.BytesIO()
    workbook.save(buffer)

    with pytest.raises(ImportFileError, match="department"):
        parse_import_file("employees.xlsx", buffer.getvalue())
