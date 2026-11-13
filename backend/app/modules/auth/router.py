"""`/api/auth/*` (T-M1-13)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.db.session import get_db_session
from app.models.account import Account
from app.models.employee import Employee
from app.modules.auth import ldap_service, local_service
from app.modules.auth.embedded_mode import is_embedded_mode
from app.modules.auth.password_policy import get_password_policy_hints, validate_password_strength
from app.modules.auth.rbac import get_current_account_allowing_password_change
from app.modules.auth.tokens import AuthResult
from app.modules.business_rules.business_day import business_today
from app.modules.business_rules.service import PASSWORD_MIN_LENGTH, get_int
from app.schemas.auth import (
    LoginRequest,
    MeResponse,
    PasswordChangeRequest,
    PasswordPolicyHintOut,
    TokenResponse,
)
from app.schemas.errors import error_responses

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _to_token_response(result: AuthResult) -> TokenResponse:
    return TokenResponse(
        access_token=result.access_token,
        role=result.role,
        must_change_password=result.must_change_password,
    )


# 401/403 здесь - не от зависимостей (их у входа нет), а от обработчиков `main.py`:
# `AuthenticationError` -> 401, `AccessDeniedError` -> 403 (LDAP-вход). Локальный вход
# 403 не отдаёт: `local_service` поднимает только `AuthenticationError`.
@router.post("/login", response_model=TokenResponse, responses=error_responses(401, 403))
async def login(
    payload: LoginRequest, session: AsyncSession = Depends(get_db_session)
) -> TokenResponse:
    """FR-AUTH-001/003: LDAP, если в `ldap_settings` есть активная и
    подтверждённая запись; иначе — режим внедрения, вход тем же
    логином/паролем обслуживается локально (FR-AUTH-003).

    Сотрудники с явно выбранным локальным паролем (`accounts.auth_type =
    'local'`) продолжают входить локально и при настроенном LDAP —
    диспетчеризация смотрит на способ входа из карточки, а не только на
    режим внедрения (T-M1-32)."""
    if await is_embedded_mode(session) or await local_service.uses_local_auth(
        session, payload.login
    ):
        result = await local_service.authenticate_local(
            session, payload.login, payload.password.get_secret_value()
        )
    else:
        result = await ldap_service.authenticate_via_ldap(
            session, payload.login, payload.password.get_secret_value()
        )
    return _to_token_response(result)


@router.post("/login/local", response_model=TokenResponse, responses=error_responses(401))
async def login_local(
    payload: LoginRequest, session: AsyncSession = Depends(get_db_session)
) -> TokenResponse:
    """FR-AUTH-002: всегда локальный вход, независимо от состояния LDAP —
    служебные записи и сотрудники с явно выбранным локальным паролем."""
    result = await local_service.authenticate_local(
        session, payload.login, payload.password.get_secret_value()
    )
    return _to_token_response(result)


async def _identity_context(
    session: AsyncSession, account: Account
) -> dict[str, str | None]:
    """Контекст для `validate_password_strength` берётся у ТЕКУЩЕЙ
    учётной записи (T-M1-38). Ровно одно из `employee_id`/`login`
    задано (`ck_accounts_employee_or_login`): служебная запись без
    карточки сотрудника не имеет ни e-mail, ни ФИО, и правило про них
    для неё просто не проверяется — явно, веткой ниже, а не потому что
    `Employee` окажется `None` и исключение случайно не поднимется."""
    if account.employee_id is None:
        return {"login": account.login, "email": None, "full_name": None}
    employee = await session.get(Employee, account.employee_id)
    assert employee is not None, "accounts.employee_id без карточки — нарушен инвариант"
    return {"login": None, "email": employee.email, "full_name": employee.full_name}


@router.post(
    "/password/change",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    # 401: токен недействителен или неверен текущий пароль; 403 нет - зависимость
    # `..._allowing_password_change` гейт смены пароля не применяет.
    responses=error_responses(400, 401),
)
async def change_password(
    payload: PasswordChangeRequest,
    account: Account = Depends(get_current_account_allowing_password_change),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Доступен и при `must_change_password=True` (`get_current_account_
    allowing_password_change`, T-M1-46) — иначе первичный пароль было бы
    нечем сменить."""
    if account.auth_type != "local" or account.local_password_hash is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Сменить пароль можно только для локальной учётной записи",
        )
    # PR #140 review (третий круг, замечание 2в): `current_password`
    # намеренно НЕ ограничен `MAX_PASSWORD_BYTES`, в отличие от входа
    # (`local_service.authenticate_local`) и `new_password` ниже.
    # Отличие оправдано: вызывающий уже аутентифицирован (`Depends(
    # get_current_account_allowing_password_change)`), а `account.
    # local_password_hash` по
    # построению никогда не хэширован из пароля длиннее 72 байт — все
    # четыре места, где приложение вызывает `hash_password` (здесь,
    # `bootstrap.py`, `employee_service.py` × 2), проверяют это ДО
    # хэширования. Усечение bcrypt при сверке здесь не может дать
    # совпадения, которого не было бы и без усечения: хранимому хэшу
    # соответствуют только первые ≤72 байта, а больше 72 байт от
    # `current_password` bcrypt и не увидит.
    if not verify_password(
        payload.current_password.get_secret_value(), account.local_password_hash
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный текущий пароль")

    # PR #143 review, коммит 1: WeakPasswordError, поднятая здесь, ловится
    # не локально, а сеткой уровня приложения (`main.py`, симметрично
    # `IntegrityError`/`ImportFileError`) — она строит тот же ответ
    # (400, список `PasswordPolicyViolationOut`), локальный `try/except`
    # был бы дублированием без дополнительной пользы.
    validate_password_strength(
        payload.new_password.get_secret_value(),
        min_length=await get_int(session, PASSWORD_MIN_LENGTH),
        **await _identity_context(session, account),
    )

    account.local_password_hash = hash_password(payload.new_password.get_secret_value())
    account.must_change_password = False
    await session.commit()


@router.get(
    "/password-policy",
    response_model=list[PasswordPolicyHintOut],
    responses=error_responses(401),
)
async def get_password_policy(
    _: Account = Depends(get_current_account_allowing_password_change),
    session: AsyncSession = Depends(get_db_session),
) -> list[PasswordPolicyHintOut]:
    """T-M1-45: состав действующих правил парольной политики с уже
    подставленными параметрами на русском (NFR-LOC-001), чтобы
    интерфейс не хардкодил ни тексты, ни значения (минимальная длина —
    бизнес-параметр, `T-M2-09`). Под авторизацией (`Depends(
    get_current_account_allowing_password_change)`, T-M1-46 — доступен и
    до смены первичного пароля, иначе экран смены не может показать её
    требования) — не публичный эндпоинт: состав правил сам по себе не
    секрет, но раскрывать детали механизма аутентификации без
    аутентификации не требуется."""
    min_length = await get_int(session, PASSWORD_MIN_LENGTH)
    return [
        PasswordPolicyHintOut(code=h.code, hint=h.message)
        for h in get_password_policy_hints(min_length=min_length)
    ]


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    responses=error_responses(401),
)
async def logout(_: Account = Depends(get_current_account_allowing_password_change)) -> None:
    """См. обоснование выбора (stateless JWT, не отзывается) в теле
    коммита T-M1-13: клиент обязан удалить оба токена локально. Доступен
    и до смены первичного пароля (`get_current_account_allowing_
    password_change`, T-M1-46) — выйти можно всегда."""
    return None


@router.get("/me", response_model=MeResponse, responses=error_responses(401))
async def me(
    account: Account = Depends(get_current_account_allowing_password_change),
    session: AsyncSession = Depends(get_db_session),
) -> MeResponse:
    """Доступен и до смены первичного пароля (`get_current_account_
    allowing_password_change`, T-M1-46) — сам `must_change_password`
    приходит только отсюда, гейтить чтение этого поля им самим было бы
    противоречием. `today` - бизнес-день (`NFR-LOC-002`): интерфейс узнаёт «сегодня» сервера
    отсюда, один раз на сессию (решение 38 раздела 13 требований)."""
    return MeResponse(
        id=account.id,
        role=account.role,
        is_active=account.is_active,
        must_change_password=account.must_change_password,
        employee_id=account.employee_id,
        login=account.login,
        embedded_mode=await is_embedded_mode(session),
        today=await business_today(session),
    )
