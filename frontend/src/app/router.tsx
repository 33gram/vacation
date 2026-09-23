import { Navigate, createBrowserRouter } from "react-router-dom";
import type { RouteObject } from "react-router-dom";

import { ChangePasswordPage } from "../pages/ChangePasswordPage";
import { DepartmentsPage } from "../pages/DepartmentsPage";
import { EmployeesPage } from "../pages/EmployeesPage";
import { LoginPage } from "../pages/LoginPage";
import { LdapSettingsPage } from "../pages/admin/LdapSettingsPage";
import { ProductionCalendarPage } from "../pages/admin/ProductionCalendarPage";
import { AppShell } from "./AppShell";
import { AuthGuard } from "./AuthGuard";
import { HomeRedirect } from "./HomeRedirect";
import { registerNavigate } from "./navigation";
import { RoleGuard } from "./RoleGuard";
import { SessionGate } from "./SessionGate";

// Отдельно от `createBrowserRouter(...)` ниже — тесты строят из этого же
// дерева `createMemoryRouter` (`test-utils/renderApp.tsx`), с контролем
// начального пути и БЕЗ общего на все тесты файла `window.history`
// (`createBrowserRouter` внутри одного тестового файла был бы одним и тем
// же объектом на все `it()`, и переходы одного теста утекали бы в
// следующий).
export const routes: RouteObject[] = [
  {
    path: "/login",
    element: <LoginPage />,
  },
  {
    path: "/",
    element: <AuthGuard />,
    children: [
      {
        element: <SessionGate />,
        children: [
          {
            path: "change-password",
            element: <ChangePasswordPage />,
          },
          {
            element: <AppShell />,
            children: [
              { index: true, element: <HomeRedirect /> },
              {
                path: "employees",
                element: (
                  <RoleGuard path="/employees">
                    <EmployeesPage />
                  </RoleGuard>
                ),
              },
              {
                path: "departments",
                element: (
                  <RoleGuard path="/departments">
                    <DepartmentsPage />
                  </RoleGuard>
                ),
              },
              {
                path: "admin/ldap-settings",
                element: (
                  <RoleGuard path="/admin/ldap-settings">
                    <LdapSettingsPage />
                  </RoleGuard>
                ),
              },
              {
                path: "admin/production-calendar",
                element: (
                  <RoleGuard path="/admin/production-calendar">
                    <ProductionCalendarPage />
                  </RoleGuard>
                ),
              },
              // Неизвестный путь внутри оболочки — на индекс, а не пустой
              // экран; отдельная страница 404 в этой задаче не требуется.
              { path: "*", element: <Navigate to="/" replace /> },
            ],
          },
        ],
      },
    ],
  },
];

export const router = createBrowserRouter(routes);

// `api/client.ts`'s 401-middleware уводит на `/login` императивно, не через
// React-контекст (fetch-мидлварь исполняется вне дерева компонентов) — см.
// `navigation.ts`.
registerNavigate((path) => router.navigate(path));
