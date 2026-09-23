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
  onResponse({ schemaPath, request, response }) {
    // 401 — сессия закончилась (токен истёк/недействителен, учётная запись
    // деактивирована) везде, КРОМЕ одного места: `POST /api/auth/password/
    // change` использует 401 не как признак конца сессии, а как код отказа
    // при НЕВЕРНОМ ТЕКУЩЕМ ПАРОЛЕ — пользователь всё ещё аутентифицирован
    // (иначе запрос не дошёл бы до тела эндпоинта), и это должен показать
    // сам экран смены пароля (as-is, `ui.md` §7), а не глобальный выход.
    // Различить эти два случая по коду ответа нельзя — только по эндпоинту.
    const isPasswordChangeAttempt =
      schemaPath === "/api/auth/password/change" && request.method === "POST";
    if (response.status === 401 && !isPasswordChangeAttempt) {
      clearAccessToken();
      goToLogin();
    }
    // 403 здесь намеренно не трогаем: это не конец сессии, а состояние
    // «Нет доступа к записи» (`ui.md` §4) — его показывает экран, который
    // сделал запрос, текстом, который вернул сервер.
    return response;
  },
});
