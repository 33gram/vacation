"""Разбор файлов импорта (T-M1-11, FR-EMP-003) — без обращения к БД."""

from __future__ import annotations

import datetime
import io
import zipfile

import pytest
from openpyxl import Workbook

from app.modules.employees.import_parsing import (
    EXPECTED_COLUMNS,
    MAX_FILE_SIZE_BYTES,
    MAX_ROWS,
    MAX_XLSX_UNCOMPRESSED_BYTES,
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


def test_parse_xlsx_garbage_content_is_rejected_cleanly() -> None:
    # Правильное расширение, но содержимое даже не ZIP-архив.
    with pytest.raises(ImportFileError):
        parse_import_file("employees.xlsx", b"this is not a zip file at all")


def test_parse_xlsx_truncated_archive_is_rejected_cleanly() -> None:
    content = _xlsx_bytes(("Иван Иванов", "P-1", "IT", "Инженер", "2024-01-15", "i@x.local"))
    truncated = content[: len(content) // 2]

    with pytest.raises(ImportFileError):
        parse_import_file("employees.xlsx", truncated)


def _xlsx_bytes_with_no_worksheets() -> bytes:
    """`openpyxl` отказывается СОХРАНЯТЬ книгу без ни одного видимого
    листа (`IndexError`) — единственный способ получить такой файл на
    входе разбора — собрать архив вручную, ровно так, как это сделал бы
    повреждённый или недобросовестно собранный файл."""
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type='
        '"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheets></sheets>"
        "</workbook>"
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        "</Relationships>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
    return buffer.getvalue()


def test_parse_xlsx_without_worksheets_is_rejected_cleanly() -> None:
    with pytest.raises(ImportFileError, match="worksheet"):
        parse_import_file("employees.xlsx", _xlsx_bytes_with_no_worksheets())


def _zip_bomb_bytes(uncompressed_size: int) -> bytes:
    """Реалистичной формы «бомба»: один сильно сжимаемый (повторяющийся
    байт) член архива, компрессия которого занимает миллисекунды и даёт
    файл в тысячи раз меньше заявленного размера — тот же принцип, что
    и у настоящей zip-бомбы, без необходимости держать сотни мегабайт
    в памяти самого теста."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("bomb.xml", b"A" * uncompressed_size)
    return buffer.getvalue()


def test_parse_xlsx_rejects_zip_bomb_before_decompressing() -> None:
    content = _zip_bomb_bytes(MAX_XLSX_UNCOMPRESSED_BYTES + 1024)
    # Заведомо меньше MAX_FILE_SIZE_BYTES — иначе отказ дал бы лимит на
    # размер ЗАГРУЖЕННОГО файла, а не именно проверка архива.
    assert len(content) < MAX_FILE_SIZE_BYTES

    with pytest.raises(ImportFileError, match="uncompressed"):
        parse_import_file("employees.xlsx", content)
