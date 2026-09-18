"""Общий guard для тестов, которым нужен тестовый OpenLDAP из
`infra/docker-compose.yml` (T-M1-06/07/16).

`_ldap_reachable()`/`LDAP_TEST_REQUIRED` были продублированы по одной
копии на файл (`test_ldap_service.py`, `modules/auth/test_router.py`,
`modules/admin/test_ldap_settings_router.py`), и только в первом была
защита `LDAP_TEST_REQUIRED` — то есть новый тестовый модуль с LDAP-тестами
мог снова получить молчаливый skip в CI, ничем не защищённый (PR #137
review, замечание #3). Это и есть риск, ради устранения которого
`LDAP_TEST_REQUIRED` вводился (см. c6c3b69): зелёный CI без реального
прогона LDAP-тестов.

Импортируя `requires_ldap` отсюда, а не копируя логику, любой новый файл
получает защиту автоматически.
"""

from __future__ import annotations

import os
import socket

import pytest

LDAP_TEST_HOST = os.environ.get("LDAP_TEST_HOST", "openldap")
LDAP_TEST_PORT = int(os.environ.get("LDAP_TEST_PORT", "1389"))

# В CI выставлен LDAP_TEST_REQUIRED=1: если тестовый каталог недоступен,
# тесты должны падать, а не молча пропускаться.
LDAP_TEST_REQUIRED = os.environ.get("LDAP_TEST_REQUIRED") == "1"


def ldap_reachable() -> bool:
    try:
        with socket.create_connection((LDAP_TEST_HOST, LDAP_TEST_PORT), timeout=5.0):
            return True
    except OSError:
        return False


requires_ldap = pytest.mark.skipif(
    not LDAP_TEST_REQUIRED and not ldap_reachable(),
    reason=(
        "Тестовый OpenLDAP (infra/docker-compose.yml, сервис openldap) "
        "недоступен — запускать через `make test-backend`"
    ),
)
