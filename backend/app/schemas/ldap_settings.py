"""Pydantic-схемы для `/api/admin/ldap-settings*` (T-M1-16)."""

from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict


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
    port: int = 389
    base_dn: str
    bind_dn: str
    # None/отсутствует — сохранённый пароль не трогаем (T-M1-16).
    bind_password: str | None = None
    user_match_attribute: str = "mail"
    is_active: bool = False


class LdapTestRequest(BaseModel):
    server_url: str
    port: int = 389
    base_dn: str
    bind_dn: str
    bind_password: str
    user_match_attribute: str = "mail"


class LdapTestResponse(BaseModel):
    success: bool
    detail: str
