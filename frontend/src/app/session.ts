import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api/client";
import { hasStoredAccessToken } from "./auth";

/** Источник истины о действующей сессии (T-M1-18): наличие токена в
 * хранилище само по себе ничего не гарантирует (см. `auth.hasStoredAccessToken`)
 * — только успешный `GET /api/auth/me` подтверждает, что сессия ещё
 * действует, и несёт роль/`must_change_password`/`embedded_mode`, на
 * которых строится вся оболочка. `enabled` не шлёт запрос вовсе, если
 * токена нет вообще — незачем: `AuthGuard` уже увёл бы на `/login`. */
export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const { data, response } = await apiClient.GET("/api/auth/me");
      if (!data) {
        throw new Error(`GET /api/auth/me failed with status ${response.status}`);
      }
      return data;
    },
    enabled: hasStoredAccessToken(),
    retry: false,
  });
}
