import { createBrowserRouter } from "react-router-dom";

import { DashboardPage } from "../pages/DashboardPage";
import { LoginPage } from "../pages/LoginPage";
import { AuthGuard } from "./AuthGuard";

export const router = createBrowserRouter([
  {
    path: "/login",
    element: <LoginPage />,
  },
  {
    path: "/",
    element: (
      <AuthGuard>
        <DashboardPage />
      </AuthGuard>
    ),
  },
]);
