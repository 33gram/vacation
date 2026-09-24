import { useContext } from "react";

import { SessionContext } from "./session-context";
import type { components } from "../api/generated/schema";

type MeResponse = components["schemas"]["MeResponse"];

export function useSession(): MeResponse {
  const value = useContext(SessionContext);
  if (!value) {
    throw new Error("useSession() вызван вне SessionProvider (должен быть внутри SessionGate)");
  }
  return value;
}
