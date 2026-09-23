const TOKEN_STORAGE_KEY = "vt_access_token";

// T-M1-18 / plan.md §5.2: только access-токен, в localStorage. Refresh не
// хранится — принимающего его эндпоинта нет (T-M1-35), хранить нечего.
// Каждое обращение к localStorage — в try/catch: в приватном режиме
// браузера доступ может бросить исключение, и приложение обязано
// продолжать работать в пределах вкладки, а не падать целиком.

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
    // localStorage недоступен (приватный режим и т.п.) — сессия не переживёт перезагрузку.
  }
}

export function clearAccessToken(): void {
  try {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // см. setAccessToken
  }
}

/** Токен присутствует в хранилище — НЕ означает, что сессия действующая:
 * токен мог истечь (60 минут, `jwt_access_token_ttl_minutes`) или учётная
 * запись/карточка могли быть деактивированы. Источник истины о действующей
 * сессии — ответ `GET /api/auth/me` (см. `session.ts`), не эта функция.
 * Годится только для быстрой проверки «есть смысл вообще спрашивать /me»
 * (`AuthGuard`) — до захода в оболочку. */
export function hasStoredAccessToken(): boolean {
  return getAccessToken() !== null;
}
