// T-M1-61: улики для флака «форма входа не отрисовалась после goto». В
// трассировках двух падений (прогоны 39621528337 и 39634582423) нашлось то,
// чего не было в выводе теста: `net::ERR_NETWORK_CHANGED` на запросах
// модулей Vite во время загрузки страницы. Эти слушатели выводят то же в
// лог СРАЗУ, с часами стенда, чтобы падение само называло запрос, который
// не дошёл, и время, по которому его сверяют с `ip monitor` и
// `docker events` (шаги job'а E2E в .github/workflows/ci.yml).
//
// Ничего не лечит и не меняет поведение тестов: только печатает. Вывод
// частый только при неполадке — на здоровой странице событий нет (ответы
// 4xx/5xx, упавшие запросы, ошибки и предупреждения консоли, исключения
// страницы).

import type { Page } from "@playwright/test";

const PREFIX = "[T-M1-61 page]";

export function logWithWallClock(prefix: string, message: string): void {
  console.log(`${prefix} ${new Date().toISOString()} ${message}`);
}

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
