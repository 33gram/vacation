import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";

import { installFetchMock, resetFetchHandler } from "./test-utils/fetchMock";

// T-M1-18: сервер мокается подменой fetch (без новых зависимостей — не
// msw). Устанавливается один раз здесь, ДО импорта `api/client.ts` любым
// тестовым файлом — см. `fetchMock.ts`, почему это важно именно здесь, а
// не в отдельном тесте.
installFetchMock();

afterEach(() => {
  resetFetchHandler();
  localStorage.clear();
});

// jsdom не реализует matchMedia — нужен для адаптивной сетки AntD (useBreakpoint).
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});
