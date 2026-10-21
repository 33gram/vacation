"""Локальная аутентификация по логину/e-mail и паролю (T-M1-08, FR-AUTH-002).

Логин — `employees.email` для сотрудников с явно назначенным локальным
входом либо `accounts.login` для служебных записей без карточки
сотрудника (например, bootstrap-администратор, FR-AUTH-005).
"""

from __future__ import annotations

import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    exceeds_bcrypt_input_limit,
    verify_against_dummy_hash,
    verify_password,
)
from app.core.time_utils import utcnow
from app.models.account import Account
from app.models.employee import Employee
from app.modules.auth.access_control import enforce_access
from app.modules.auth.exceptions import AccessDeniedError, AuthenticationError
from app.modules.auth.repository import (
    get_account_by_employee_id,
    get_account_by_login,
    get_employee_by_email,
)
from app.modules.auth.tokens import AuthResult, issue_tokens
from app.modules.business_rules.service import (
    LOCKOUT_DURATION_MINUTES,
    MAX_FAILED_LOGIN_ATTEMPTS,
    get_rules,
)


async def _resolve_local_account(
    session: AsyncSession, login: str
) -> tuple[Account | None, Employee | None]:
    employee = await get_employee_by_email(session, login)
    if employee is not None:
        account = await get_account_by_employee_id(session, employee.id)
        return account, employee

    account = await get_account_by_login(session, login)
    return account, None


async def uses_local_auth(session: AsyncSession, login: str) -> bool:
    """Есть ли для `login` учётная запись с явно выбранным локальным
    паролем (`accounts.auth_type = 'local'`) — диспетчер
    `POST /api/auth/login` (T-M1-32) использует это, чтобы такие
    сотрудники продолжали входить через основной эндпоинт, даже когда
    LDAP настроен и подтверждён (FR-AUTH-003), а не только через
    `/api/auth/login/local`."""
    account, _ = await _resolve_local_account(session, login)
    return account is not None and account.auth_type == "local"


async def _register_failed_attempt(session: AsyncSession, account: Account) -> None:
    account.failed_login_attempts += 1
    # FR-AUTH-002/2 (T-M2-09): порог и срок блокировки — параметры из
    # `business_rules_config`, читаются одним запросом на неуспешную
    # попытку; значения из кода сюда не подставляются.
    #
    # ТАЙМИНГ (FR-AUTH-002/14, PR #176 review). Эта ветка (существующая
    # учётная запись, неверный пароль) платит то, чего не платит ветка
    # «логина нет»: UPDATE + COMMIT (старше T-M2-09) и этот SELECT.
    # Замер `POST /api/auth/login/local`, 200 пар на серию, по две серии на
    # дерево, внутри контейнера, ASGI-транспорт без сети, bcrypt ≈ 236 мс:
    # разность медиан «существующий − несуществующий» на `main` +1,29 и
    # +0,90 мс, на ветке +1,59 и +1,90 мс (0,4–0,8 % стоимости bcrypt);
    # межквартильный разброс одиночного запроса 2,5–8 мс. Вклад SELECT'а
    # этой разностью НЕ устанавливается (стандартная ошибка разности
    # медиан при таком разбросе и 200 парах — 0,2–0,7 мс, то есть порядка
    # самого вклада), поэтому он измерен напрямую: `get_rules` двух ключей
    # — медиана 0,23–0,24 мс против 0,13–0,14 мс у `SELECT 1` на том же
    # соединении (три прогона по 3000 вызовов, чередуются). Это 0,1 % от
    # bcrypt и меньше разброса одного запроса, поэтому чтение параметров
    # НЕ поднято выше ветвления (оно не вернуло бы разницу к нулю:
    # UPDATE + COMMIT остаются). Наблюдатель по сети получает не более
    # `max_failed_login_attempts` замеров существующей записи ЗА ОДНО ОКНО
    # БЛОКИРОВКИ (по истечении срока замеры доступны снова), и каждое окно
    # заканчивается ОТДЕЛЬНЫМ сообщением, раскрывающим факт сам по себе
    # (решение 26 раздела 13 требований); набрать статистику на разницу
    # в доли процента в таком окне ему не из чего. Числа и метод — T-M2-09
    # в docs/tasks.md; замер ручной, не CI-проверка.
    rules = await get_rules(session, MAX_FAILED_LOGIN_ATTEMPTS, LOCKOUT_DURATION_MINUTES)
    if account.failed_login_attempts >= rules[MAX_FAILED_LOGIN_ATTEMPTS]:
        account.locked_until = utcnow() + datetime.timedelta(
            minutes=rules[LOCKOUT_DURATION_MINUTES]
        )
    await session.commit()


async def authenticate_local(session: AsyncSession, login: str, password: str) -> AuthResult:
    if not password or not password.strip():
        # Пустой/пробельный пароль отклоняется до любого обращения к БД или
        # каталогу (FR-AUTH-004) — см. ldap_service.authenticate_via_ldap.
        # FR-AUTH-002/14 это НЕ закрывает и не должен: разница по времени
        # здесь зависит от присланного пароля, а не от учётной записи, и
        # не сообщает наблюдателю ничего о системе — вне объёма критерия.
        raise AuthenticationError("Invalid login or password")

    if exceeds_bcrypt_input_limit(password):
        # PR #140 review (второй круг), FR-AUTH-002 (раздел 13, п. 20):
        # `MAX_PASSWORD_BYTES` раньше проверялся только при задании/смене
        # пароля (`validate_password_strength`), которую вход не
        # вызывает — `verify_password`/`bcrypt.checkpw` ниже усекает вход
        # так же, как хэширование усекает пароль при задании. Без этой
        # проверки легальный пароль ровно на границе плюс произвольный
        # хвост после неё аутентифицировал бы ту же учётную запись.
        # PR #140 review (третий круг, замечание 2б): отказ здесь — то же
        # СООБЩЕНИЕ и тот же СТАТУС (401), что и у обычного неверного
        # пароля. Разница по времени ответа здесь ЕСТЬ и сознательно не
        # выравнивается (FR-AUTH-002/14, раздел 13, п. 26) — она зависит
        # от присланного ПАРОЛЯ (его длины), а не от учётной записи, и
        # ничего не раскрывает о системе наблюдателю, который сам же эту
        # длину и прислал.
        raise AuthenticationError("Invalid login or password")

    account, employee = await _resolve_local_account(session, login)

    if account is None or account.auth_type != "local":
        # PR #140 review (третий круг, замечание 2а): то же СООБЩЕНИЕ и
        # тот же СТАТУС (401), что и у существующего логина с неверным
        # паролем. Разница по времени здесь ЗАВИСИТ ОТ УЧЁТНОЙ ЗАПИСИ
        # (нет записи либо способ входа не локальный) — ровно то, что
        # выравнивает FR-AUTH-002/14 (T-M1-41): без сверки с фиктивным
        # хэшем фиксированной стоимости здесь, bcrypt не вызывался бы
        # вовсе, а в ветке `verify_password` ниже вызывается всегда —
        # разница порядка ~100 мс (стоимость bcrypt) против микросекунд
        # отличила бы существующий логин от несуществующего по времени
        # ответа так же явно, как если бы отличалось само сообщение.
        verify_against_dummy_hash(password)
        raise AuthenticationError("Invalid login or password")

    if account.locked_until is not None and account.locked_until > utcnow():
        # FR-AUTH-002/14 (раздел 13, п. 26) сознательно НЕ выравнивает эту
        # ветку: сообщение ниже само по себе, без всякого времени ответа,
        # уже раскрывает факт блокировки — решение в пользу легального
        # пользователя, которому иначе пришлось бы гадать, почему верный
        # пароль не принимается. Выравнивание времени при уже раскрытом
        # текстом факте было бы декоративным, не защитой.
        raise AuthenticationError("Account temporarily locked, try again later")

    if account.local_password_hash is None:
        # Зависит от учётной записи (локальный хэш не задан) — тот же
        # случай FR-AUTH-002/14, что и «нет записи» выше, отдельная ветка
        # именно потому, что иначе `or` короткого замыкания в условии не
        # дал бы `verify_password` ни разу выполниться для этого пути.
        verify_against_dummy_hash(password)
        await _register_failed_attempt(session, account)
        raise AuthenticationError("Invalid login or password")

    if not verify_password(password, account.local_password_hash):
        await _register_failed_attempt(session, account)
        raise AuthenticationError("Invalid login or password")

    account.failed_login_attempts = 0
    account.locked_until = None

    try:
        await enforce_access(session, account, employee=employee)
    except AccessDeniedError:
        await session.commit()
        raise

    await session.commit()
    return issue_tokens(account)
