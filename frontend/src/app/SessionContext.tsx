import type { ReactNode } from "react";

import { SessionContext } from "./session-context";
import type { components } from "../api/generated/schema";

type MeResponse = components["schemas"]["MeResponse"];

export function SessionProvider({
  value,
  children,
}: {
  value: MeResponse;
  children: ReactNode;
}) {
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
