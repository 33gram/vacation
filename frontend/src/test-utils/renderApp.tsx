import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
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
export function renderApp(initialPath = "/") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const testRouter = createMemoryRouter(routes, { initialEntries: [initialPath] });
  registerNavigate((path) => testRouter.navigate(path));

  const result = render(
    <QueryClientProvider client={queryClient}>
      <ConfigProvider locale={ruRU}>
        <RouterProvider router={testRouter} />
      </ConfigProvider>
    </QueryClientProvider>,
  );

  return { ...result, router: testRouter };
}
