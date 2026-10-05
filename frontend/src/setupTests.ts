import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";
import { afterEach } from "vitest";

import { installFetchMock, resetFetchHandler } from "./test-utils/fetchMock";

// T-M1-19 raised this from the testing-library default (1000ms) to
// 5000ms to paper over CPU contention under parallel-file test runs -
// T-M1-54 reversed that: `vite.config.ts`'s `fileParallelism: false`
// removes the contention this was compensating for (that file's comment
// has the measured numbers), so this goes back to the default these
// pages were actually written against.
configure({ asyncUtilTimeout: 1000 });

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
