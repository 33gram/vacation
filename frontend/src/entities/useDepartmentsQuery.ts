import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api/client";

/** `GET /api/departments` — ЕДИНСТВЕННОЕ место (PR #153 review, п. 3):
 * было продублировано в трёх страницах (`EmployeesPage`,
 * `EmployeeCardPage`, `EmployeeCreatePage`) с одним и тем же ключом
 * кэша, но по одной опечатке в любой копии разошлись бы. */
export function useDepartmentsQuery() {
  return useQuery({
    queryKey: ["departments"],
    queryFn: async () => {
      const { data, response } = await apiClient.GET("/api/departments");
      if (!data) {
        throw new Error(`GET /api/departments failed with status ${response.status}`);
      }
      return data;
    },
  });
}
