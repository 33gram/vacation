import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api/client";

/** `GET /api/employees` с `include_terminated: true` — ЕДИНСТВЕННЫЙ
 * ключ кэша (`["employees", true]`) для этого запроса во всём разделе
 * (PR #153 review, п. 3): `EmployeesPage`, `EmployeeCardPage` и
 * `EmployeeCreatePage` держали три копии этого хука, и ключ кэша уже
 * разошёлся между ними ("employees", includeTerminated] против
 * ["employees", true]) — правка одной копии молча переставала
 * инвалидировать другую при `invalidateQueries({queryKey: ["employees"]})`.
 * `true`, а не параметр — список сам решает, кого показывать
 * (`EmployeesPage`'s клиентский фильтр «Показывать уволенных»), а
 * карточке и форме нужен ПОЛНЫЙ список для разрешения ФИО руководителя,
 * даже уволенного. */
export function useAllEmployeesQuery() {
  return useQuery({
    queryKey: ["employees", true],
    queryFn: async () => {
      const { data, response } = await apiClient.GET("/api/employees", {
        params: { query: { include_terminated: true } },
      });
      if (!data) {
        throw new Error(`GET /api/employees failed with status ${response.status}`);
      }
      return data;
    },
  });
}
