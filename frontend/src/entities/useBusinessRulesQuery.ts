import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api/client";

/** Ключ кэша реестра параметров: на него же пишет `PUT` (ответ несёт реестр целиком). */
export const BUSINESS_RULES_QUERY_KEY = ["business-rules"] as const;

/** `GET /api/admin/business-rules` - весь реестр параметров в порядке реестра. */
export function useBusinessRulesQuery() {
  return useQuery({
    queryKey: BUSINESS_RULES_QUERY_KEY,
    queryFn: async () => {
      const { data, response } = await apiClient.GET("/api/admin/business-rules");
      if (!data) {
        throw new Error(`GET /api/admin/business-rules failed with status ${response.status}`);
      }
      return data;
    },
  });
}
