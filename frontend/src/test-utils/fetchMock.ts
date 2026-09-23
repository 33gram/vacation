import { vi } from "vitest";

/** Обработчик одного запроса теста: получает метод и путь (без origin —
 * `baseUrl` клиента пуст, T-M1-18) и решает, что ответить. `null` — запрос
 * этому тесту не интересен и не ожидался, тест должен упасть явной
 * ошибкой, а не тихо повиснуть. */
export type FetchHandler = (
  method: string,
  pathname: string,
  init: RequestInit,
) => { status: number; body?: unknown } | null;

let currentHandler: FetchHandler | null = null;

/** Вызывается из теста — задаёт, как отвечать на запросы ДО следующего
 * вызова (сбрасывается автоматически между тестами, см. `setupTests.ts`). */
export function setFetchHandler(handler: FetchHandler): void {
  currentHandler = handler;
}

export function resetFetchHandler(): void {
  currentHandler = null;
}

/** Устанавливается ОДИН раз, из `setupTests.ts`, ДО импорта модулей
 * приложения: `openapi-fetch.createClient()` читает `globalThis.fetch` (и
 * `globalThis.Request`, см. ниже) один раз, в момент создания клиента
 * (`api/client.ts`, исполняется при первом импорте) — застабить их уже
 * ПОСЛЕ того, как клиент создан, бесполезно, он этого не увидит. Поэтому
 * подменяется не сама функция на каждый тест, а её ПОВЕДЕНИЕ — через
 * `currentHandler`, задаваемый заново в каждом тесте. */
export function installFetchMock(): void {
  // Реальный браузер резолвит относительный URL в `fetch`/`new Request(...)`
  // против текущей страницы — production-код (`api/client.ts`, `baseUrl:
  // ""`, T-M1-18) на это и рассчитан. В jsdom/Node глобальный `Request` (не
  // патченный jsdom под конкретную страницу) такого не делает и падает на
  // относительном пути ("Failed to parse URL") — стенд, а не баг клиента.
  // Патч только для тестов, по тому же принципу, что и `matchMedia` ниже.
  const OriginalRequest = globalThis.Request;
  class TestRequest extends OriginalRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      const absolute =
        typeof input === "string" && input.startsWith("/")
          ? new URL(input, "http://localhost")
          : input;
      super(absolute, init);
    }
  }
  vi.stubGlobal("Request", TestRequest);

  const dispatcher: typeof fetch = async (input, init) => {
    const url = new URL(
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url,
      "http://localhost",
    );
    const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();

    if (!currentHandler) {
      throw new Error(`Fetch mock not configured for ${method} ${url.pathname} — call setFetchHandler()`);
    }
    const result = currentHandler(method, url.pathname, init ?? {});
    if (!result) {
      throw new Error(`Unhandled request in test: ${method} ${url.pathname}`);
    }
    return new Response(result.body === undefined ? null : JSON.stringify(result.body), {
      status: result.status,
    });
  };

  vi.stubGlobal("fetch", dispatcher);
}
