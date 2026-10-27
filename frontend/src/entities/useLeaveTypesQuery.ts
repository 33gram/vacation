import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api/client";

/** `GET /api/leave-types` - справочник видов отсутствий. Отдельного `GET` на один вид в API нет:
 * карточка вида берёт запись из этого же списка (`LeaveTypeCardPage`). Единственный ключ кэша
 * `["leave-types"]` на весь раздел. */
export function useLeaveTypesQuery() {
  return useQuery({
    queryKey: ["leave-types"],
    queryFn: async () => {
      const { data, response } = await apiClient.GET("/api/leave-types");
      if (!data) {
        throw new Error(`GET /api/leave-types failed with status ${response.status}`);
      }
      return data;
    },
  });
}
