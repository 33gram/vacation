import { createContext, useContext } from "react";
import type { ReactNode } from "react";

import type { components } from "../api/generated/schema";

type MeResponse = components["schemas"]["MeResponse"];

// Заполняется один раз, в `SessionGate` (после успешного `GET /api/auth/
// me`), и читается ниже по дереву (`AppShell`, `RoleGuard`, страницы) — так
// вложенным маршрутам не нужно перезапрашивать `/me` самим.
const SessionContext = createContext<MeResponse | null>(null);

export function SessionProvider({
  value,
  children,
}: {
  value: MeResponse;
  children: ReactNode;
}) {
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): MeResponse {
  const value = useContext(SessionContext);
  if (!value) {
    throw new Error("useSession() вызван вне SessionProvider (должен быть внутри SessionGate)");
  }
  return value;
}
