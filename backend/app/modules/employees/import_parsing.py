"""Разбор файлов импорта сотрудников (T-M1-11, FR-EMP-003).

Не знает про БД — задача этого модуля: превратить файл (CSV/XLSX) в
список строк с их номером в файле. Проверка значений полей, поиск
подразделения/руководителя и запись — в `import_service.py`.
"""

from __future__ import annotations

import csv
import datetime
import io
import zipfile
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

# Жёсткие лимиты на загруженный файл (NFR-SCALE-001: штат до 750
# человек — запас с кратным отрывом, не больше). `MAX_FILE_SIZE_BYTES`
# меряет размер файла КАК ЗАГРУЖЕН — для XLSX это СЖАТЫЙ размер архива,
# а `MAX_ROWS` считает строки ПОСЛЕ разбора — ни то, ни другое не
# защищает от zip-бомбы: 293 КБ архива, разворачивающегося в ~600 МБ в
# памяти на `load_workbook`, проходит оба лимита (проверено экспериментом,
# не рассуждением — см. `CLAUDE.md`, правило 8). Собственно защита от
# этого — `_reject_oversized_xlsx_archive` ниже, ДО вызова `openpyxl`.
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MiB
MAX_ROWS = 1000

# Порог на СУММАРНЫЙ ЗАЯВЛЕННЫЙ (несжатый) размер записей архива —
# десятки-сотни килобайт для 750 строк по 9 текстовых полей (даже с
# многократной наценкой на разметку XML/служебные части XLSX) укладываются
# на два порядка ниже этого порога; 20 МиБ — щедрый запас для легитимного
# файла и одновременно на порядки меньше сотен мегабайт из сценария атаки.
# Заявленный размер берётся из ЦЕНТРАЛЬНОГО КАТАЛОГА ZIP — до разворачивания
# содержимого, поэтому дешева проверка не требует самой распаковки. Но это
# заявление отправителя архива, а не факт: `zipfile`/`openpyxl` не проверяют
# его достоверность при чтении, и вредоносный архив может указать заниженное
# значение, а по факту развернуться в большее. Проверка — снижение риска
# для типичных генераторов zip-бомб (актуальный сценарий: обычный HR,
# `hr_admin`/`system_admin`, а не целенаправленная атака в обход, — DoS
# силами своей же учётной записи), а не гарантия против намеренно
# подделанных метаданных архива.
MAX_XLSX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024  # 20 MiB


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
        raise ImportFileError(f"Файл превышает предельный размер {MAX_FILE_SIZE_BYTES} байт")

    lower_name = filename.lower()
    if lower_name.endswith(".csv"):
        rows = _parse_csv(content)
    elif lower_name.endswith(".xlsx"):
        rows = _parse_xlsx(content)
    else:
        raise ImportFileError("Неподдерживаемый тип файла: ожидается .csv или .xlsx")

    if len(rows) > MAX_ROWS:
        raise ImportFileError(f"Файл превышает предельное количество строк данных ({MAX_ROWS})")

    return rows


def _require_expected_columns(header: list[str]) -> None:
    missing = [column for column in EXPECTED_COLUMNS if column not in header]
    if missing:
        raise ImportFileError(f"Отсутствуют обязательные колонки: {', '.join(missing)}")


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
        raise ImportFileError("Файл пуст") from None
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


def _reject_oversized_xlsx_archive(content: bytes) -> None:
    """До передачи файла в `openpyxl` (PR #139 review, замечание 2 оси
    стандартов) — читает ТОЛЬКО центральный каталог ZIP (список записей
    с их размерами), не распаковывая содержимое, и отказывает, если
    суммарный заявленный несжатый размер превышает `MAX_XLSX_
    UNCOMPRESSED_BYTES`. См. обоснование порога и границы этой проверки
    там же. Битый архив (не ZIP вовсе) здесь не отклоняется — эту
    ошибку даёт `_parse_xlsx` при вызове `load_workbook`, с тем же
    единообразным `ImportFileError`."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            total_uncompressed = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile:
        return
    if total_uncompressed > MAX_XLSX_UNCOMPRESSED_BYTES:
        raise ImportFileError(
            f"Архив XLSX заявляет {total_uncompressed} байт в распакованном виде — "
            f"это больше предельных {MAX_XLSX_UNCOMPRESSED_BYTES} байт"
        )


def _parse_xlsx(content: bytes) -> list[ParsedRow]:
    """Любая ошибка разбора — отказ файлу (`ImportFileError`), а не
    необработанное исключение (PR #139 review, замечание 2): `openpyxl`
    поднимает разные типы на разных стадиях для разных видов поломки
    (`zipfile.BadZipFile` на битом архиве, `KeyError`/`XMLSyntaxError`-
    подобные на повреждённом XML внутри валидного архива), а не только
    свой `InvalidFileException` — перечислять их поимённо означало бы
    гарантированно забыть один. Собственный `ImportFileError` (например,
    из `_require_expected_columns`) при этом не подменяется."""
    _reject_oversized_xlsx_archive(content)

    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:
        raise ImportFileError(f"Не удалось прочитать XLSX-файл: {exc}") from exc

    try:
        sheet = workbook.active
        if sheet is None:
            raise ImportFileError("В файле XLSX нет ни одного листа")
        rows_iter = sheet.iter_rows(values_only=True)
        try:
            header_row = next(rows_iter)
        except StopIteration:
            raise ImportFileError("Файл пуст") from None
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
    except ImportFileError:
        raise
    except Exception as exc:
        raise ImportFileError(f"Не удалось прочитать XLSX-файл: {exc}") from exc
    finally:
        workbook.close()
