"""Хэширование локальных паролей (bcrypt, NFR-SEC-004) и выпуск/проверка
JWT-сессий (PyJWT, FR-AUTH-001/002)."""

from __future__ import annotations

import datetime
import unicodedata
from functools import lru_cache
from typing import Any

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.time_utils import utcnow

# PR #140 review (четвёртый круг): 72 байта — свойство bcrypt (он молча
# хэширует только первые 72 байта входа, не отклоняя более длинный),
# а не парольной политики приложения, поэтому граница живёт здесь, а не
# в `modules/auth/password_policy.py`. Три места приложения, которым
# нужно было проверить это ДО вызова bcrypt (парольная политика при
# задании/смене пароля, вход, bootstrap-администратор), трижды
# передержали проверку заново вместо общего предиката — граница стоила
# трёх кругов ревью именно поэтому, а не из-за расхождения копий числа
# 72 между ними.
BCRYPT_MAX_INPUT_BYTES = 72


def normalize_password(password: str) -> str:
    """`FR-AUTH-002/13` (T-M1-42): единая точка приведения пароля к форме
    Unicode NFC. «é» в форме NFC (один символ, U+00E9) и в форме NFD
    («e» + комбинирующий акут U+0301) — разные байтовые строки разной
    длины; без нормализации пароль, заданный на одной раскладке/ОС, мог
    не подойти на другой, а проверки политики давали разные ответы на
    один и тот же пароль в зависимости от формы ввода.

    Не единственный вызов во всей кодовой базе (см. также
    `validate_password_strength`, `modules/auth/password_policy.py`) —
    это каждая из НИЗКОУРОВНЕВЫХ функций, через которые обязан пройти
    любой путь, трогающий байты пароля (`exceeds_bcrypt_input_limit`,
    `hash_password`, `verify_password` здесь и `validate_password_
    strength` там); ни `local_service.authenticate_local`, ни
    `bootstrap.ensure_bootstrap_admin`, ни `router.change_password` не
    трогают пароль напрямую — они вызывают эти четыре функции, то есть
    покрыты транзитивно, без собственного вызова. Проверка, что все
    четыре функции действительно вызывают `normalize_password`, —
    `backend/tests/test_password_normalization_coverage.py` (по
    аналогии с `test_endpoint_auth_coverage.py`/`test_constraints_
    applied.py` — перечисление по сигнатуре функции, не договорённость).
    """
    return unicodedata.normalize("NFC", password)


def exceeds_bcrypt_input_limit(password: str) -> bool:
    password = normalize_password(password)
    return len(password.encode("utf-8")) > BCRYPT_MAX_INPUT_BYTES


def hash_password(password: str) -> str:
    password = normalize_password(password)
    if exceeds_bcrypt_input_limit(password):
        # Сетка НЕ вместо дружелюбных проверок выше по стеку (парольная
        # политика, вход, bootstrap) — они остаются и дают понятное
        # сообщение раньше. Это срабатывает только тогда, когда новый
        # путь к `hash_password` их не вызвал, то есть на баге, а не на
        # штатном отказе пользователю.
        raise ValueError(
            f"Password is {len(password.encode('utf-8'))} bytes when UTF-8 encoded, "
            f"over bcrypt's {BCRYPT_MAX_INPUT_BYTES}-byte input limit — reject it "
            "before hashing instead of letting bcrypt silently truncate it"
        )
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    password = normalize_password(password)
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # некорректный формат хэша — не должно случаться, но не роняем логин
        return False


@lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    """`FR-AUTH-002/14` (T-M1-41): точка сравнения для сверки
    ФИКСИРОВАННОЙ СТОИМОСТИ там, где настоящего хэша нет (нет учётной
    записи, способ входа LDAP, локальный хэш не задан) — без неё эти
    пути короткого замыкания отвечают за микросекунды против ~100 мс
    настоящей сверки bcrypt, и разница по времени ответа столь же
    красноречива, что и прямое сообщение о несуществующей учётной
    записи.

    Получена тем же `hash_password`, которым хэшируются настоящие
    пароли, — НЕ захардкожена: если параметр стоимости bcrypt
    (`gensalt()`) когда-нибудь изменится, фиктивный хэш изменится вместе
    с настоящими сам, и выравнивание не станет косметическим. Кэширована
    (`lru_cache`, один раз за процесс) — это не секрет конкретного входа
    и не хэш ничьего реального пароля, а неизменная точка сравнения;
    пересчитывать её на каждый запрос значило бы платить за bcrypt
    дважды без всякой пользы."""
    return hash_password("FR-AUTH-002/14 fixed-cost comparison target, not a real password")


def verify_against_dummy_hash(password: str) -> None:
    """`FR-AUTH-002/14` (T-M1-41): вызывается на путях входа,
    зависящих от учётной записи, где раскрывать её отсутствие/вид по
    времени ответа нельзя, а настоящего хэша для сравнения нет. Результат
    игнорируется намеренно — единственная цель вызова здесь оплатить ту
    же стоимость bcrypt, что платит `verify_password` на настоящем
    хэше, а не получить ответ."""
    verify_password(password, _dummy_password_hash())


class TokenError(Exception):
    """JWT не прошёл декодирование/проверку подписи или срока действия."""


def create_access_token(*, account_id: int, role: str) -> str:
    settings = get_settings()
    now = utcnow()
    payload = {
        "sub": str(account_id),
        "role": role,
        "type": "access",
        "iat": now,
        "exp": now + datetime.timedelta(minutes=settings.jwt_access_token_ttl_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(*, account_id: int) -> str:
    settings = get_settings()
    now = utcnow()
    payload = {
        "sub": str(account_id),
        "type": "refresh",
        "iat": now,
        "exp": now + datetime.timedelta(days=settings.jwt_refresh_token_ttl_days),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
