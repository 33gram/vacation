import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";
import { afterEach } from "vitest";

import { installFetchMock, resetFetchHandler } from "./test-utils/fetchMock";

// T-M1-19: `findBy*`/`waitFor`'s own default (1000ms) is independent of
// vitest's `testTimeout` and started missing intermittently once the
// suite grew antd-Form-heavy pages (EmployeeForm-based create/edit/import) -
// under the default parallel-file run, an unrelated test's render
// competes for the same CPU and can genuinely take past 1000ms to settle;
// confirmed by 100% reliable passes with `vitest run --no-file-
// parallelism` at the OLD default, i.e. contention, not a real hang.
configure({ asyncUtilTimeout: 5000 });

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
