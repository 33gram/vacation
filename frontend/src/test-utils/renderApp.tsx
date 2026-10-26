import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { StrictMode } from "react";
import { ConfigProvider } from "antd";
import ruRU from "antd/locale/ru_RU";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import { registerNavigate } from "../app/navigation";
import { routes } from "../app/router";

/** Рендерит приложение целиком (то же дерево маршрутов, что и настоящее
 * `router.tsx`), но через `createMemoryRouter` с собственной историей и
 * собственным `QueryClient` — не через `<App/>`'s синглтон-роутер и
 * синглтон-`QueryClient` (`app/providers.tsx`), которые были бы ОДНИМ и
 * тем же объектом на все тесты одного файла: переходы/закешированные
 * запросы одного теста утекали бы в следующий. `registerNavigate`
 * перерегистрируется на КАЖДЫЙ рендер — иначе 401-мидлварь клиента
 * (`api/client.ts`) продолжала бы звать роутер предыдущего теста. */
export function renderApp(
  initialPath = "/",
  { strictMode = false, retry = false }: { strictMode?: boolean; retry?: boolean | number } = {},
) {
  const queryClient = new QueryClient({
    // `retry` - для теста, которому нужен промышленный умолчательный повтор `QueryClient`
    // (он повторяет упавший запрос трижды): по умолчанию в тестах повторов нет.
    defaultOptions: { queries: { retry, retryDelay: 1 } },
  });
  const testRouter = createMemoryRouter(routes, { initialEntries: [initialPath] });
  registerNavigate((path) => testRouter.navigate(path));

  const tree = (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider locale={ruRU}>
        <RouterProvider router={testRouter} />
      </ConfigProvider>
    </QueryClientProvider>
  );
  // `strictMode` - как в `main.tsx`: в режиме разработки React монтирует компонент дважды.
  const result = render(strictMode ? <StrictMode>{tree}</StrictMode> : tree);

  return { ...result, router: testRouter, queryClient };
}
