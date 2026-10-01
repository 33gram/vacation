import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api/client";

/** `GET /api/admin/production-calendar/{year}` (`T-M1-22`, `FR-ADMIN-003`)
 * — отвечает 200 и с пустым `days`, если год ещё не загружен (не 404,
 * `production_calendar_service.get_year`). Хук не различает «не загружен»
 * от «загружен, но действительно пуст» — для API это один и тот же
 * ответ, и разбор длины `days` — дело вызывающей стороны
 * (`ProductionCalendarPage`), не этого хука. Ключ кэша включает год —
 * переключение года не переиспользует чужие данные и не требует ручной
 * инвалидации при смене. */
export function useProductionCalendarQuery(year: number) {
  return useQuery({
    queryKey: ["production-calendar", year],
    queryFn: async () => {
      const { data, response } = await apiClient.GET("/api/admin/production-calendar/{year}", {
        params: { path: { year } },
      });
      if (!data) {
        throw new Error(
          `GET /api/admin/production-calendar/${year} failed with status ${response.status}`,
        );
      }
      return data;
    },
  });
}
