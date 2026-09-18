"""Общий guard для тестов, которым нужен тестовый OpenLDAP из
`infra/docker-compose.yml` (T-M1-06/07/16).

`_ldap_reachable()` была продублирована по одной копии на файл
(`test_ldap_service.py`, `modules/auth/test_router.py`,
`modules/admin/test_ldap_settings_router.py`) — `requires_ldap` отсюда
устраняет это дублирование, но сам по себе не может гарантировать, что
*новый* файл с LDAP-тестами его вообще импортирует (PR #137 review,
узкое ревью): файл со своей собственной, неправильной проверкой
доступности каталога прошёл бы мимо этого guard'а незамеченным.

Поэтому запрет на молчаливый пропуск в CI живёт не здесь, а уровнем
выше — в `tests/conftest.py` (`PYTEST_FORBID_SKIPS`), где граница
проведена через собственный учёт pytest, а не через список случаев
(см. докстринг модуля там): механизм заваливает прогон ровно на том, что
pytest сам считает пропуском, — независимо от того, LDAP-ный это тест
или нет и идёт ли пропуск через этот guard. Здесь остаётся ровно то, для
чего guard изначально создавался: не копировать проверку доступности в
три файла. Локально (без `PYTEST_FORBID_SKIPS`) пропуск при недоступном
каталоге по-прежнему разрешён — это осознанное поведение, не дыра
в защите.
"""

from __future__ import annotations

import os
import socket

import pytest

LDAP_TEST_HOST = os.environ.get("LDAP_TEST_HOST", "openldap")
LDAP_TEST_PORT = int(os.environ.get("LDAP_TEST_PORT", "1389"))


def ldap_reachable() -> bool:
    try:
        with socket.create_connection((LDAP_TEST_HOST, LDAP_TEST_PORT), timeout=5.0):
            return True
    except OSError:
        return False


requires_ldap = pytest.mark.skipif(
    not ldap_reachable(),
    reason=(
        "Тестовый OpenLDAP (infra/docker-compose.yml, сервис openldap) "
        "недоступен — запускать через `make test-backend`"
    ),
)
