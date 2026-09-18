"""Pydantic-схемы для `/api/admin/ldap-settings*` (T-M1-16)."""

from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict, SecretStr

from app.core.ldap_client import DEFAULT_LDAP_PORT, DEFAULT_USER_MATCH_ATTRIBUTE


class LdapSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    server_url: str
    port: int
    base_dn: str
    bind_dn: str
    user_match_attribute: str
    is_active: bool
    last_tested_at: datetime.datetime | None
    last_test_status: str | None


class LdapSettingsIn(BaseModel):
    server_url: str
    port: int = DEFAULT_LDAP_PORT
    base_dn: str
    bind_dn: str
    # None/отсутствует — сохранённый пароль не трогаем (T-M1-16).
    bind_password: str | None = None
    user_match_attribute: str = DEFAULT_USER_MATCH_ATTRIBUTE
    is_active: bool = False


class LdapTestRequest(BaseModel):
    """Все поля необязательны и по умолчанию `None` — так эндпоинт
    `/ldap-settings/test` различает два случая:

    - тело пустое (все поля `None`) и параметры уже сохранены — проверяется
      именно сохранённая запись (её собственный расшифрованный пароль), и
      результат фиксируется в `last_tested_at`/`last_test_status`;
    - указано хотя бы одно поле — проверяются черновые/произвольные
      параметры (недостающие берутся из сохранённой записи, как в PUT),
      результат нигде не сохраняется."""

    server_url: str | None = None
    port: int | None = None
    base_dn: str | None = None
    bind_dn: str | None = None
    # SecretStr: в OpenAPI-схеме backend'а это writeOnly-строка, а не
    # обычная (TypeScript такого понятия не знает, openapi-typescript его
    # не переносит — в сгенерированном клиенте поле остаётся обычным
    # `string | null`, это ожидаемо). От утечки самого пароля защищает не
    # тип клиента, а то, что его вообще нет в LdapSettingsOut; SecretStr
    # здесь лишь маскирует repr()/str(), если это когда-нибудь случайно
    # попадёт в лог.
    bind_password: SecretStr | None = None
    user_match_attribute: str | None = None


class LdapTestResponse(BaseModel):
    success: bool
    detail: str
