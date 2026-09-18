from __future__ import annotations

from dataclasses import dataclass

from app.core.security import create_access_token, create_refresh_token
from app.models.account import Account


@dataclass(frozen=True)
class AuthResult:
    access_token: str
    refresh_token: str
    role: str
    must_change_password: bool


def issue_tokens(account: Account) -> AuthResult:
    return AuthResult(
        access_token=create_access_token(account_id=account.id, role=account.role),
        refresh_token=create_refresh_token(account_id=account.id),
        role=account.role,
        must_change_password=account.must_change_password,
    )
