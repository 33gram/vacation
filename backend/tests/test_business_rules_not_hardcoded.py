"""Параметры парольной политики не подставляются из кода (T-M2-09).

`password_policy.py` — чистые функции: значение им ПЕРЕДАЮТ, и в чистых
функциях у `min_length` остаётся значение по умолчанию (на нём стоят тесты
самих функций). Это значит, что вызывающий код МОГ БЫ его забыть, и тогда
молча действовало бы значение из кода, а не из `business_rules_config`.
Эта проверка читает дерево разбора `app/` и краснеет, если

  1. вызов `validate_password_strength`/`get_password_policy_hints` не
     передаёт `min_length=` именованным аргументом;
  2. константы `DEFAULT_MIN_PASSWORD_LENGTH`/`DEFAULT_MAX_FAILED_ATTEMPTS`/
     `DEFAULT_LOCKOUT_DURATION` используются где-либо в `app/` вне
     `password_policy.py`.

ГРАНИЦА (правило 8): проверка видит прямой вызов по имени и импорт по
имени. Вызов через псевдоним (`import ... as`), `getattr` или передачу
функции как значения она не увидит; подключение вызова в новом модуле
ревью обязано проверить так же, как оно проверяет остальное.
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent / "app"
_POLICY_MODULE = _APP_ROOT / "modules" / "auth" / "password_policy.py"
_PURE_FUNCTIONS = {"validate_password_strength", "get_password_policy_hints"}
_DEFAULT_CONSTANTS = {
    "DEFAULT_MIN_PASSWORD_LENGTH",
    "DEFAULT_MAX_FAILED_ATTEMPTS",
    "DEFAULT_LOCKOUT_DURATION",
}


def find_violations(source: str, filename: str) -> list[str]:
    problems = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in _PURE_FUNCTIONS and not any(kw.arg == "min_length" for kw in node.keywords):
                problems.append(f"{filename}:{node.lineno}: вызов {name} без min_length=")
        names = []
        if isinstance(node, ast.Name):
            names = [node.id]
        elif isinstance(node, ast.Attribute):
            names = [node.attr]
        elif isinstance(node, ast.ImportFrom):
            names = [alias.name for alias in node.names]
        problems += [
            f"{filename}:{node.lineno}: использование {name} вне password_policy.py"
            for name in names
            if name in _DEFAULT_CONSTANTS
        ]
    return problems


def _app_sources() -> list[Path]:
    return [path for path in sorted(_APP_ROOT.rglob("*.py")) if path != _POLICY_MODULE]


def test_production_code_never_relies_on_the_in_code_password_defaults() -> None:
    problems = []
    for path in _app_sources():
        problems += find_violations(
            path.read_text(encoding="utf-8"), str(path.relative_to(_APP_ROOT))
        )

    assert problems == []


def test_a_call_without_min_length_is_reported() -> None:
    source = "validate_password_strength(password, email=e)\nget_password_policy_hints()\n"

    assert find_violations(source, "x.py") == [
        "x.py:1: вызов validate_password_strength без min_length=",
        "x.py:2: вызов get_password_policy_hints без min_length=",
    ]


def test_a_call_with_min_length_is_not_reported() -> None:
    source = (
        "validate_password_strength(password, min_length=n)\n"
        "mod.get_password_policy_hints(min_length=n)\n"
    )

    assert find_violations(source, "x.py") == []


def test_use_of_an_in_code_default_is_reported() -> None:
    source = (
        "from app.modules.auth.password_policy import DEFAULT_MAX_FAILED_ATTEMPTS\n"
        "x = DEFAULT_MAX_FAILED_ATTEMPTS\n"
    )

    assert find_violations(source, "x.py") == [
        "x.py:1: использование DEFAULT_MAX_FAILED_ATTEMPTS вне password_policy.py",
        "x.py:2: использование DEFAULT_MAX_FAILED_ATTEMPTS вне password_policy.py",
    ]
