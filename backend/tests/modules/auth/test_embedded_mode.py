from app.core.config import get_settings
from app.core.crypto import encrypt
from app.models.ldap_settings import LdapSettings
from app.modules.auth.embedded_mode import is_embedded_mode


def _ldap_settings(*, is_active: bool, last_test_status: str | None = None) -> LdapSettings:
    return LdapSettings(
        server_url="openldap",
        port=1389,
        base_dn="dc=company,dc=local",
        bind_dn="cn=admin,dc=company,dc=local",
        bind_password_encrypted=encrypt(
            "changeme", key=get_settings().ldap_password_encryption_key
        ),
        is_active=is_active,
        last_test_status=last_test_status,
    )


async def test_embedded_mode_true_when_no_ldap_settings_exist(db_session) -> None:
    assert await is_embedded_mode(db_session) is True


async def test_embedded_mode_true_when_active_but_not_yet_verified(db_session) -> None:
    # FR-AUTH-003: сохранение (`is_active=true`) само по себе не выводит
    # из режима внедрения — нужна ещё и успешная проверка.
    db_session.add(_ldap_settings(is_active=True, last_test_status=None))
    await db_session.flush()

    assert await is_embedded_mode(db_session) is True


async def test_embedded_mode_true_when_active_and_last_check_failed(db_session) -> None:
    db_session.add(_ldap_settings(is_active=True, last_test_status="failure"))
    await db_session.flush()

    assert await is_embedded_mode(db_session) is True


async def test_embedded_mode_false_when_active_and_verified(db_session) -> None:
    db_session.add(_ldap_settings(is_active=True, last_test_status="success"))
    await db_session.flush()

    assert await is_embedded_mode(db_session) is False


async def test_embedded_mode_true_when_ldap_settings_inactive_even_if_verified(db_session) -> None:
    db_session.add(_ldap_settings(is_active=False, last_test_status="success"))
    await db_session.flush()

    assert await is_embedded_mode(db_session) is True
