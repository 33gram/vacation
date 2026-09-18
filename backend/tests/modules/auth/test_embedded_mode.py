from app.core.config import get_settings
from app.core.crypto import encrypt
from app.models.ldap_settings import LdapSettings
from app.modules.auth.embedded_mode import is_embedded_mode


def _ldap_settings(*, is_active: bool) -> LdapSettings:
    return LdapSettings(
        server_url="openldap",
        port=1389,
        base_dn="dc=company,dc=local",
        bind_dn="cn=admin,dc=company,dc=local",
        bind_password_encrypted=encrypt(
            "changeme", key=get_settings().ldap_password_encryption_key
        ),
        is_active=is_active,
    )


async def test_embedded_mode_true_when_no_ldap_settings_exist(db_session) -> None:
    assert await is_embedded_mode(db_session) is True


async def test_embedded_mode_false_when_active_ldap_settings_exist(db_session) -> None:
    db_session.add(_ldap_settings(is_active=True))
    await db_session.flush()

    assert await is_embedded_mode(db_session) is False


async def test_embedded_mode_true_when_ldap_settings_inactive(db_session) -> None:
    db_session.add(_ldap_settings(is_active=False))
    await db_session.flush()

    assert await is_embedded_mode(db_session) is True
