import type { ReactElement } from "react";
import { Navigate } from "react-router-dom";

import { isAuthenticated } from "./auth";

export function AuthGuard({ children }: { children: ReactElement }): ReactElement {
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace />;
  }

  return children;
}
