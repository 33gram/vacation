import type { ReactElement } from "react";
import { Navigate, Outlet } from "react-router-dom";

import { hasStoredAccessToken } from "./auth";

/** Только наличие токена — не проверяет действующую сессию (это делает
 * `SessionGate`, следующим уровнем ниже, через `GET /api/auth/me`).
 * Достаточно на этом уровне: если токена нет вовсе, спрашивать `/me`
 * незачем — 401 неизбежен. */
export function AuthGuard(): ReactElement {
  if (!hasStoredAccessToken()) {
    return <Navigate to="/login" replace />;
  }

  return <Outlet />;
}
