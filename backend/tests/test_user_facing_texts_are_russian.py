"""T-M1-47, NFR-LOC-001: сообщение, которое дойдёт до пользователя в теле
HTTP-ответа, обязано быть по-русски (`ui.md` §7 запрещает переписывать
серверные формулировки на клиенте — значит по-русски они должны прийти
С СЕРВЕРА).

Ограничение этой проверки — она не про "весь возможный путь ответа", а
ровно про строковые литералы в модулях `_SCANNED_MODULES` ниже, в местах,
которые эти модули используют для ошибок, реально доходящих до
пользователя (`HTTPException`, `ValueError`/`ImportFileError`/
`EmployeeNotFoundError`/`DepartmentNotFoundError`, поле `reason` у
`RowError`). Она НЕ проверяет:
- модули, для которых русский текст не требование (`_EXCLUDED_MODULES`
  ниже — «что не переводится», см. тело коммита T-M1-47);
- встроенные тексты pydantic в 422 (`"Field required"` и подобные) —
  это не наши строки, их правка — T-M1-40 (`RequestValidationError`);
- машиночитаемые идентификаторы (имена полей вроде `personnel_number`,
  `email`, значения `auth_type`) — они и не должны быть переведены;
  проверка целенаправленно смотрит только на ТЕКСТ сообщения (реальный
  аргумент-«причина»/«сообщение» вызова), а не на любой строковый
  литерал в файле;
- любой путь ответа за пределами перечисленных модулей — если текст
  окажется английским где-то ещё, эта проверка об этом не узнает.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parent.parent

# Закрытый список — модули, которые задача T-M1-47 переводит (докстринг
# коммита перечисляет их поимённо, это не "все файлы бэкенда"):
# HTTPException (rbac.py/router.py/employee_router.py), ValueError-тексты
# (employee_service.py, department_service.py,
# production_calendar_service.py, ldap_settings_service.py,
# unique_violations.py, schemas/employee.py), сообщения импорта
# (import_parsing.py/import_service.py), LdapAuthError (core/ldap_client.py
# — PR #149 review: возвращается администратору как есть через
# `POST /api/admin/ldap-settings/test`, пропущено в первом заходе).
_SCANNED_MODULES = (
    "app/modules/auth/rbac.py",
    "app/modules/auth/router.py",
    "app/modules/employees/employee_router.py",
    "app/modules/employees/employee_service.py",
    "app/modules/employees/department_service.py",
    "app/modules/admin/production_calendar_service.py",
    "app/modules/admin/ldap_settings_service.py",
    "app/modules/employees/unique_violations.py",
    "app/schemas/employee.py",
    "app/modules/employees/import_parsing.py",
    "app/modules/employees/import_service.py",
    "app/core/ldap_client.py",
)

# «Что не переводится» (тело коммита T-M1-47) — намеренно НЕ входят в
# `_SCANNED_MODULES`, перечислены здесь, чтобы граница проверки была
# явной, а не «забыли добавить»:
# - core/config.py — валидация настроек при старте, адресат —
#   администратор развёртывания, не пользователь;
# - core/security.py — внутренний инвариант, срабатывает только на баге;
# - modules/auth/password_policy.py — `PasswordPolicyViolation.__post_
#   init__`, сетка для разработчика.
_EXCLUDED_MODULES = (
    "app/core/config.py",
    "app/core/security.py",
    "app/modules/auth/password_policy.py",
)

# Имя вызываемого — позиция(и) аргумента(ов), которые несут ТЕКСТ
# сообщения (а не код ответа, номер строки или имя поля).
_MESSAGE_ARG_BY_CALLEE: dict[str, tuple[int, ...]] = {
    "HTTPException": (1,),
    "ValueError": (0,),
    "ImportFileError": (0,),
    "EmployeeNotFoundError": (0,),
    "DepartmentNotFoundError": (0,),
    "LdapAuthError": (0,),
    "RowError": (2,),
}

_CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")


def _literal_text(node: ast.expr) -> str | None:
    """Собирает буквальный текст узла — только СВОИ фрагменты f-строки
    (`ast.Constant` внутри `ast.JoinedStr`), не значения `{...}` — они
    данные запроса, а не текст сообщения, и им не обязательно быть
    кириллицей (табельный номер, e-mail, id). Конкатенация (`"a: " +
    "; ".join(...)`) собирается рекурсивно по левому/правому операнду."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if isinstance(v, ast.Constant))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_text(node.left)
        right = _literal_text(node.right)
        if left is None or right is None:
            return None
        return left + right
    return None


def _find_english_messages(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = getattr(node.func, "id", None)
        arg_positions = _MESSAGE_ARG_BY_CALLEE.get(callee)
        if arg_positions is None:
            continue
        for position in arg_positions:
            if position >= len(node.args):
                continue
            text = _literal_text(node.args[position])
            # `None` — не строковый литерал этой формы (переменная,
            # вызов функции и т. п.) — не наш случай, пропускаем, а не
            # считаем нарушением: проверка смотрит литералы, а не любой
            # источник текста.
            if text is None:
                continue
            # Пустая или чисто техническая строка (например, разделитель
            # без единого содержательного слова) не может «быть на
            # английском» осмысленно — но реальные сообщения этих
            # модулей всегда длиннее нескольких символов, поэтому порог
            # не отфильтровывает ничего настоящего.
            if len(text.strip()) <= 2:
                continue
            if not _CYRILLIC.search(text):
                offenders.append(f"{path.relative_to(_BACKEND_ROOT)}: {callee}(...): {text!r}")
    return offenders


def test_scanned_modules_are_in_the_repository() -> None:
    """Отрицательный контроль на сам список: опечатка в пути молча
    исключила бы модуль из проверки навсегда (`_find_english_messages`
    просто не нашла бы файл и ничего не сообщила бы)."""
    for rel_path in _SCANNED_MODULES:
        assert (_BACKEND_ROOT / rel_path).is_file(), rel_path


@pytest.mark.parametrize("rel_path", _SCANNED_MODULES)
def test_user_facing_messages_contain_cyrillic(rel_path: str) -> None:
    offenders = _find_english_messages(_BACKEND_ROOT / rel_path)
    assert offenders == [], (
        "английский текст в сообщении, которое дойдёт до пользователя "
        f"(NFR-LOC-001): {offenders}"
    )
