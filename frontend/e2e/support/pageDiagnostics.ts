// T-M1-61: улики для флака «форма входа не отрисовалась после goto». В
// трассировках двух падений (прогоны 39621528337 и 39634582423) нашлось то,
// чего не было в выводе теста: `net::ERR_NETWORK_CHANGED` на запросах
// модулей Vite во время загрузки страницы. Эти слушатели выводят то же в
// лог СРАЗУ, с часами стенда, чтобы падение само называло запрос, который
// не дошёл, и время, по которому его сверяют с `ip monitor` и
// `docker events` (шаги job'а E2E в .github/workflows/ci.yml).
//
// Ничего не лечит и не меняет поведение тестов: только печатает. Строки
// появляются на каждой загрузке страницы (ответ на документ: статус, URL,
// размер) и при неполадке (ответы 4xx/5xx, упавшие запросы, ошибки и
// предупреждения консоли, исключения страницы). «Здоровая» страница тоже
// не молчит: отрицательные контроли входа дают ожидаемые 401 от
// `POST /api/auth/login`, и зелёный прогон их печатает.
//
// Ответ на документ пишется в момент загрузки, а не после падения: по нему
// следующий повтор отличает «стек не ответил» (нет строки или статус ≥ 500)
// от «ответил, но модули оборваны» (статус 200, размер разметки обычный,
// дальше `requestfailed`).

import { test as base, type Page } from "@playwright/test";

import { logWithWallClock } from "./wallClock";

const PREFIX = "[T-M1-61 page]";

export function watchPage(page: Page): void {
  const log = (message: string) => logWithWallClock(PREFIX, message);
  page.on("requestfailed", (request) => {
    log(
      `requestfailed ${request.method()} ${request.url()} - ${request.failure()?.errorText}`,
    );
  });
  page.on("response", (response) => {
    if (response.status() >= 400) {
      log(
        `response ${response.status()} ${response.request().method()} ${response.url()}`,
      );
    }
    if (
      response.request().isNavigationRequest() &&
      response.frame() === page.mainFrame()
    ) {
      // Тело читается в обработчике события: сбой чтения (страница уже
      // ушла) не должен ронять тест, поэтому он тоже только печатается.
      response
        .body()
        .then((body) => log(`document ${response.status()} ${response.url()} html=${body.length} bytes`))
        .catch((error: Error) =>
          log(`document ${response.status()} ${response.url()} html=unreadable (${error.message})`),
        );
    }
  });
  page.on("console", (message) => {
    if (message.type() === "error" || message.type() === "warning") {
      log(
        `console.${message.type()} ${message.text()} (${message.location().url})`,
      );
    }
  });
  page.on("pageerror", (error) => {
    log(`pageerror ${error.message}`);
  });
}

// `test` с подключённой печатью: спеки импортируют его отсюда вместо
// `@playwright/test`, и каждая страница теста наблюдается без повтора
// `beforeEach` в каждом файле.
export const test = base.extend({
  page: async ({ page }, provide) => {
    watchPage(page);
    await provide(page);
  },
});
