import createClient from "openapi-fetch";

import { clearAccessToken, getAccessToken } from "../app/auth";
import { goToLogin } from "../app/navigation";
import type { paths } from "./generated/schema";

// T-M1-18: пути в сгенерированном клиенте уже содержат "/api/..."
// (см. schema.d.ts) — дефолт здесь пустая строка, а не "/api", иначе первый
// же вызов ушёл бы в "/api/api/auth/login". Заглушка авторизации (T-M0-10)
// backend не звала, поэтому расхождение раньше не всплывало. Пустая строка
// работает потому, что `infra/nginx/nginx.dev.conf` отдаёт фронт и "/api/"
// с одного origin — относительный путь достаточен.
export const apiClient = createClient<paths>({
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? "",
});

apiClient.use({
  onRequest({ request }) {
    const token = getAccessToken();
    if (token) {
      request.headers.set("Authorization", `Bearer ${token}`);
    }
    return request;
  },
  async onResponse({ schemaPath, request, response }) {
    // 401 — сессия закончилась (токен истёк/недействителен, учётная запись
    // деактивирована) везде, КРОМЕ одного места: `POST /api/auth/password/
    // change` использует 401 ЕЩЁ и как код отказа при НЕВЕРНОМ ТЕКУЩЕМ
    // ПАРОЛЕ — на уже аутентифицированном запросе (иначе он не дошёл бы до
    // тела эндпоинта, `get_current_account` отказал бы раньше). Но тот же
    // `get_current_account` может отказать 401 и НА ЭТОМ САМОМ запросе —
    // если человек просидел на экране первого входа дольше TTL access-
    // токена (60 минут): тогда это снова конец сессии, тем же кодом.
    // Различить эти два случая по коду ответа НЕЛЬЗЯ (T-M1-47 к тому же
    // сделает и тексты неотличимыми) — только переспросив `/me`.
    const isPasswordChangeAttempt =
      schemaPath === "/api/auth/password/change" && request.method === "POST";
    if (response.status === 401) {
      if (isPasswordChangeAttempt) {
        // 401 у `/me` заново входит в эту же мидлварь — с `schemaPath`
        // `/api/auth/me`, то есть НЕ `isPasswordChangeAttempt` — и уходит в
        // ветку ниже (очистка + выход) сама. 200 у `/me` — сессия жива,
        // это действительно неверный текущий пароль: ответ password/change
        // возвращается вызывающему коду как есть, без вмешательства.
        await apiClient.GET("/api/auth/me");
      } else {
        clearAccessToken();
        goToLogin();
      }
    }
    // 403 здесь намеренно не трогаем: это не конец сессии, а состояние
    // «Нет доступа к записи» (`ui.md` §4) — его показывает экран, который
    // сделал запрос, текстом, который вернул сервер.
    return response;
  },
});
