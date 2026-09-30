import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api/client";

/** `GET /api/admin/production-calendar/{year}` (`T-M1-22`, `FR-ADMIN-003`)
 * — отвечает 200 и с пустым `days`, если год ещё не загружен (не 404):
 * различать «год не загружен» и «загружен, но задача не для этого экрана»
 * незачем, оба ответа успешны и различаются только длиной `days`,
 * которую разбирает вызывающая сторона (`ProductionCalendarPage`), не
 * этот хук. Ключ кэша включает год — переключение года не переиспользует
 * чужие данные и не требует ручной инвалидации при смене. */
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
