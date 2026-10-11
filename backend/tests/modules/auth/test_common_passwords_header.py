"""`backend/app/modules/auth/common_passwords.txt`'s own header (T-M1-39,
`requirements.md` раздел 13, п. 28) называет два числа — 10001 запись
без изменений из SecLists и 79 кириллических записей после
разделителя блоков. Это решение владельца продукта: кириллический
блок не заменяется на источник с измеренной статистикой на этапе
разработки, а шапка файла — заявленный источник правды о составе на
этот период. Документ, который называет неверные числа, перестаёт
быть таким источником.

Числа РАЗБИРАЮТСЯ из самого текста шапки, не продублированы здесь
константами — иначе чисел стало бы четыре вместо двух, и расходиться
могли бы уже они сами, а не шапка с файлом. Разбор опирается на то же
свойство файла, которым пользуется сам загрузчик
(`_load_common_passwords`, `password_policy.py`): пустые строки и
строки-комментарии пропускаются ВЕЗДЕ, не только в начале, — этим и
устроен разделитель между блоками записей посередине файла, не номером
строки.

Честная граница (правило 8, CLAUDE.md): проверка сравнивает число
записей с числом, которое шапка НАЗЫВАЕТ, — не содержательный состав
записей (что именно изменилось внутри блока при равном числе строк).
Такая подмена этим тестом не поймана и не должна считаться пойманной.
"""

from __future__ import annotations

import re

from app.modules.auth.password_policy import _COMMON_PASSWORDS_PATH

_SECLISTS_COUNT_PATTERN = re.compile(r"(\d+)\s+запись\s+без изменений\s*—\s*SecLists")
_CYRILLIC_COUNT_PATTERN = re.compile(r"(\d+)\s+записей\s+ПОСЛЕ разделителя")


def _is_comment_or_blank(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _counts_named_in_header(text: str) -> tuple[int, int]:
    seclists_match = _SECLISTS_COUNT_PATTERN.search(text)
    cyrillic_match = _CYRILLIC_COUNT_PATTERN.search(text)
    assert seclists_match is not None, (
        "шапка не называет число записей SecLists в ожидаемой форме "
        '("N запись без изменений — SecLists") - разбор сломан или формулировку поменяли'
    )
    assert cyrillic_match is not None, (
        "шапка не называет число кириллических записей в ожидаемой форме "
        '("N записей ПОСЛЕ разделителя") - разбор сломан или формулировку поменяли'
    )
    return int(seclists_match.group(1)), int(cyrillic_match.group(1))


def _actual_block_sizes(text: str) -> tuple[int, int]:
    """Опирается на то же свойство, что `_load_common_passwords`: пустые
    строки и `#`-комментарии пропускаются ВЕЗДЕ, не только в начале, -
    разделитель между блоками сам такая строка, не номер строки."""
    lines = text.splitlines()
    i = 0

    # Заголовочный блок комментариев в начале файла - пропускается целиком.
    while i < len(lines) and _is_comment_or_blank(lines[i]):
        i += 1

    before = 0
    while i < len(lines) and not _is_comment_or_blank(lines[i]):
        before += 1
        i += 1

    # Разделитель между блоками (и любые пустые строки рядом с ним).
    separator_seen = False
    while i < len(lines) and _is_comment_or_blank(lines[i]):
        if lines[i].strip().startswith("#"):
            separator_seen = True
        i += 1
    assert separator_seen, (
        "не найдена строка-разделитель между блоками записей - "
        "устройство файла изменилось, разбор теста больше не соответствует"
    )

    after = 0
    while i < len(lines):
        if not _is_comment_or_blank(lines[i]):
            after += 1
        i += 1

    return before, after


def test_header_counts_match_actual_entry_counts() -> None:
    text = _COMMON_PASSWORDS_PATH.read_text(encoding="utf-8")

    named_seclists, named_cyrillic = _counts_named_in_header(text)
    actual_seclists, actual_cyrillic = _actual_block_sizes(text)

    assert actual_seclists == named_seclists, (
        f"шапка называет {named_seclists} записей до разделителя, "
        f"фактически их {actual_seclists} - состав изменился без правки шапки "
        f"(или шапка правилась без проверки факта)"
    )
    assert actual_cyrillic == named_cyrillic, (
        f"шапка называет {named_cyrillic} кириллических записей после разделителя, "
        f"фактически их {actual_cyrillic} - состав изменился без правки шапки "
        f"(или шапка правилась без проверки факта)"
    )
