const TOKEN_STORAGE_KEY = "vt_access_token";

// Заглушка авторизации (T-M0-10): токен читается/пишется в localStorage без
// реального обращения к backend. Настоящий вход через LDAP/локальный —
// FR-AUTH-001…004, этап M1.

export function getAccessToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setAccessToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_STORAGE_KEY, token);
  } catch {
    // localStorage недоступен (приватный режим и т.п.) — сессия не переживёт перезагрузку
  }
}

export function clearAccessToken(): void {
  try {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // см. setAccessToken
  }
}

export function isAuthenticated(): boolean {
  return getAccessToken() !== null;
}
