"""`FR-AUTH-002/13` (T-M1-42): локальный пароль приводится к Unicode NFC
до применения парольной политики, до хэширования и до сверки при входе
— единообразно во всех путях (задание, смена, включая `current_password`,
вход, первичный пароль администратора). «é» в форме NFC (один символ,
U+00E9) и в форме NFD («e» + комбинирующий акут U+0301) — разные
байтовые строки разной длины; каждый тест этого файла строит пару таких
форм ОДНОГО И ТОГО ЖЕ логического пароля и проверяет, что система даёт
одинаковый ответ независимо от того, в какой форме он пришёл.

Отрицательный контроль (убрать `normalize_password(...)` из всех четырёх
точек приведения и прогнать этот файл) приведён в теле коммита, не
здесь — восстанавливается сразу после прогона, в файле не остаётся.
"""

from __future__ import annotations

import unicodedata

import pytest

from app.core.config import Settings, get_settings
from app.core.security import exceeds_bcrypt_input_limit, hash_password, verify_password
from app.modules.auth import password_policy
from app.modules.auth.bootstrap import ensure_bootstrap_admin
from app.modules.auth.password_policy import WeakPasswordError, validate_password_strength
from app.modules.auth.repository import get_account_by_login
from tests.modules.auth.factories import bearer_header, make_account, make_employee

NFC_PASSWORD = "Caféword1!"  # "Caféword1!" - "é" целым символом (U+00E9)
NFD_PASSWORD = unicodedata.normalize("NFD", NFC_PASSWORD)


def _violation_codes(exc: WeakPasswordError) -> set[str]:
    return {v.code for v in exc.violations}


def test_nfc_and_nfd_fixtures_are_actually_different_bytes() -> None:
    """Самопроверка фикстур выше, подтверждена прогоном, а не
    предположена: без этого остальные тесты файла могли бы молча
    сравнивать два одинаковых байтовых значения и ничего не доказывать."""
    assert NFC_PASSWORD != NFD_PASSWORD
    assert len(NFC_PASSWORD) < len(NFD_PASSWORD)


# --- Хэширование и сверка при входе -----------------------------------


def test_nfd_password_authenticates_against_hash_created_from_nfc_form() -> None:
    password_hash = hash_password(NFC_PASSWORD)
    assert verify_password(NFD_PASSWORD, password_hash)


def test_nfc_password_authenticates_against_hash_created_from_nfd_form() -> None:
    password_hash = hash_password(NFD_PASSWORD)
    assert verify_password(NFC_PASSWORD, password_hash)


# --- Решения политики совпадают для NFC- и NFD-формы ------------------


def test_length_boundary_decision_matches_for_nfc_and_nfd_forms() -> None:
    """Пароль, который в НОРМАЛИЗОВАННОЙ (NFC) форме на один символ
    короче минимальной длины, а в NFD-форме, посимвольно, минимальную
    длину формально набирает — базовый символ и отдельный комбинирующий
    знак считаются раздельно без нормализации. До правки непронормали-
    зованная длина NFD-формы ложно проходила бы границу, тогда как
    NFC-форма закономерно её не проходила — одно и то же решение
    пользователя получало бы два разных ответа."""
    nfc_password = "Caféf1!"  # "Caféf1!" - 7 символов в NFC
    nfd_password = unicodedata.normalize("NFD", nfc_password)
    assert len(nfc_password) == 7
    assert len(nfd_password) == 8  # подтверждено прогоном, не предположено

    for password in (nfc_password, nfd_password):
        with pytest.raises(WeakPasswordError) as exc_info:
            validate_password_strength(password, min_length=8)
        assert password_policy.VIOLATION_TOO_SHORT in _violation_codes(exc_info.value)


def test_identity_rule_decision_matches_for_nfc_and_nfd_forms() -> None:
    """Запрет e-mail в пароле (`FR-AUTH-002/8`) сравнивает подстроки —
    без нормализации пароля его NFD-форма не содержала бы e-mail
    (хранящийся в карточке сотрудника в обычной, NFC форме) как
    подстроку, хотя семантически это тот же адрес: разные байты для
    «é». NFC-форма пароля правило ловило бы и без приведения, NFD —
    обходила бы."""
    email_local_part = "josé"  # "josé", предсоставленная форма
    email = f"{email_local_part}@company.local"
    nfc_password = email_local_part + "1234!A"
    nfd_password = unicodedata.normalize("NFD", nfc_password)
    assert nfc_password != nfd_password

    for password in (nfc_password, nfd_password):
        with pytest.raises(WeakPasswordError) as exc_info:
            validate_password_strength(password, email=email)
        assert password_policy.VIOLATION_CONTAINS_IDENTITY in _violation_codes(exc_info.value)


def test_common_password_decision_matches_for_nfc_and_nfd_forms(monkeypatch) -> None:
    """Список распространённых паролей (`FR-AUTH-002/9`) сравнивается
    строкой целиком — ни одна настоящая запись файла не содержит
    составной латинский акцент (проверено: `grep` по списку пуст), так
    что соответствующая строка подставлена через monkeypatch, а не взята
    из `common_passwords.txt`. Без нормализации NFD-форма не совпала бы
    с записью, хранящейся в NFC, — список обходился бы."""
    monkeypatch.setattr(password_policy, "COMMON_PASSWORDS", frozenset({"café1234"}))
    nfc_password = "Café1234"  # "Café1234" - 3 класса (регистр+регистр+цифра), состав пройден
    nfd_password = unicodedata.normalize("NFD", nfc_password)
    assert nfc_password != nfd_password

    for password in (nfc_password, nfd_password):
        with pytest.raises(WeakPasswordError) as exc_info:
            validate_password_strength(password)
        assert password_policy.VIOLATION_COMMON_PASSWORD in _violation_codes(exc_info.value)


def test_composition_decision_matches_for_nfc_and_nfd_forms() -> None:
    """Состав символов (`FR-AUTH-002/6`) уже не зависел от формы ДО этой
    задачи: комбинирующий знак не даёт статуса спецсимвола ни
    приложенным к букве (NFC), ни отдельно (NFD) — буква в обоих случаях
    уже даёт класс строчных/заглавных сама по себе, независимо от
    того, нормализован ли пароль. Проверено прогоном: единственный из
    тестов этого файла, который уже проходил и без `normalize_password`
    (см. тело коммита) — включён ради явной фиксации инварианта, а не
    как отрицательный контроль."""
    nfc_password = "password1É"  # "É" (precomposed) - третий класс, состав пройден
    nfd_password = unicodedata.normalize("NFD", nfc_password)
    assert nfc_password != nfd_password

    for password in (nfc_password, nfd_password):
        validate_password_strength(password)  # не поднимает ни на одной форме


def test_72_byte_boundary_crosses_only_in_nfd_form() -> None:
    """Наблюдаемое следствие решения `FR-AUTH-002/13`: пароль, чья NFD-
    форма превышает 72-байтовую границу (`FR-AUTH-002/4`, `/5`), а NFC-
    форма — нет. Числа подтверждены прогоном, а не подобраны на глаз."""
    nfc_password = "A1!" + "é" * 34  # "é" x34 после короткого ASCII-префикса
    nfd_password = unicodedata.normalize("NFD", nfc_password)
    nfc_bytes = len(nfc_password.encode("utf-8"))
    nfd_bytes = len(nfd_password.encode("utf-8"))
    assert nfc_bytes <= 72 < nfd_bytes, (nfc_bytes, nfd_bytes)

    assert not exceeds_bcrypt_input_limit(nfc_password)
    assert not exceeds_bcrypt_input_limit(nfd_password)


# --- Смена пароля: новая форма при задании, другая при входе ----------


async def test_password_changed_in_nfd_form_logs_in_with_nfc_form(db_session, api_client) -> None:
    """Смена пароля (`POST /api/auth/password/change`) через HTTP:
    `new_password` задан в NFD, последующий вход — в NFC той же
    последовательности. `current_password` тоже проходит через
    `verify_password` (нормализуется там же), поэтому старый пароль
    задан обычной ASCII-строкой — его форма этому тесту не нужна."""
    employee = await make_employee(db_session, email="normalize-change@company.local")
    account = await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password("OldPassword1!"),
        must_change_password=True,
    )

    change_response = await api_client.post(
        "/api/auth/password/change",
        json={"current_password": "OldPassword1!", "new_password": NFD_PASSWORD},
        headers=bearer_header(account),
    )
    assert change_response.status_code == 204

    login_response = await api_client.post(
        "/api/auth/login/local",
        json={"login": "normalize-change@company.local", "password": NFC_PASSWORD},
    )
    assert login_response.status_code == 200


# --- bootstrap-администратор --------------------------------------------


async def test_bootstrap_admin_password_in_nfd_form_logs_in_via_nfc_form(
    db_session, monkeypatch
) -> None:
    """`BOOTSTRAP_ADMIN_PASSWORD` в не-ASCII форме (FR-AUTH-005) — тот же
    выбор нормализации, что и для обычного локального пароля, проверен
    отдельно, потому что у bootstrap-администратора нет карточки
    сотрудника и он не проходит через `validate_password_strength`
    (см. `bootstrap.ensure_bootstrap_admin`), только через
    `hash_password`/`verify_password`."""
    bootstrap_settings = Settings(_env_file=None, bootstrap_admin_password=NFD_PASSWORD)
    monkeypatch.setattr("app.modules.auth.bootstrap.get_settings", lambda: bootstrap_settings)

    await ensure_bootstrap_admin(db_session)

    account = await get_account_by_login(db_session, bootstrap_settings.bootstrap_admin_login)
    assert account is not None
    assert verify_password(NFC_PASSWORD, account.local_password_hash)


async def test_default_ascii_bootstrap_password_is_unaffected_by_normalization(
    db_session,
) -> None:
    """Подтверждение из теста, не объявление (T-M1-42, п. 5): боевых
    хэшей в БД нет, а единственный хэш в dev-базе создан из ASCII-строки
    — для ASCII NFC-нормализация не меняет байты вовсе
    (`unicodedata.normalize("NFC", s) == s` для любой ASCII `s`), так что
    вход bootstrap-администратора по умолчанию обязан продолжать
    работать без каких-либо изменений."""
    settings = get_settings()
    assert settings.bootstrap_admin_password.isascii()

    await ensure_bootstrap_admin(db_session)

    account = await get_account_by_login(db_session, settings.bootstrap_admin_login)
    assert account is not None
    assert verify_password(settings.bootstrap_admin_password, account.local_password_hash)
