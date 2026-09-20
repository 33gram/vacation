"""Конструктор поддельного `IntegrityError` для тестов сетки на
неожиданные нарушения целостности (PR #139 review).

`unique_violations.unique_violation_field` читает `exc.orig.__cause__.
constraint_name` — воспроизводит ровно эту форму настоящей ошибки
asyncpg, не саму ошибку целиком, чтобы тесты могли имитировать гонку на
`COMMIT`/`flush` без реального нарушения ограничения в БД.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError


class _FakeCause(Exception):
    def __init__(self, constraint_name: str) -> None:
        super().__init__()
        self.constraint_name = constraint_name


class _FakeOrig(Exception):
    def __init__(self, constraint_name: str) -> None:
        super().__init__()
        self.__cause__ = _FakeCause(constraint_name)


def fake_integrity_error(constraint_name: str) -> IntegrityError:
    return IntegrityError("statement", {}, _FakeOrig(constraint_name))
