import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api/client";
import { stringDetail } from "../api/errors";

/** Отказ загрузки остатка: статус нужен странице, чтобы отличить «Нет доступа» (403) от
 * «Ошибка загрузки» (всё остальное). Текст сервера - как есть (`ui.md` §7). */
export class BalancesLoadError extends Error {
  readonly status: number;
  readonly detail: string | null;

  constructor(status: number, detail: string | null) {
    super(`GET /api/employees/{id}/balances failed with status ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

/** `GET /api/employees/{id}/balances`. ВНИМАНИЕ: этот `GET` ПИШЕТ в журнал - ленивое
 * начисление перед чтением (`T-M2-19`), коммит внутри запроса. Повтор безопасен (повторное
 * начисление ничего не пишет), но запросы не множатся: все автоматические повторы выключены
 * явно, а не оставлены умолчаниям `QueryClient` (в промышленной сборке он повторяет упавший
 * запрос трижды; в тестах - нет, поэтому умолчание тест не видит).
 *   - `retry: false` - упавший запрос повторяет только кнопка «Повторить»;
 *   - `refetchOnWindowFocus`/`refetchOnReconnect: false` - возврат на вкладку не пишет в журнал;
 *   - `enabled: employeeId !== null` - без карточки (`employee_id = null`) запрос не уходит сам по
 *     себе, а не потому что соседний компонент (`RoleGuard`) не пустил на страницу;
 *   - один запрос на МОНТИРОВАНИЕ: хук вызывается из одного места страницы, а два наблюдателя
 *     за одним ключом (React в режиме разработки монтирует компонент дважды) разделяют один
 *     запрос в полёте; перерисовка без размонтирования запроса не даёт.
 * Уход в другой раздел и возврат - это новое монтирование и ВТОРОЙ пишущий запрос. Вреда
 * нет: повторное начисление ничего не пишет (свойство `accrue_leave`, `T-M2-19`, проверено
 * backend-тестами, не этим файлом), поэтому второй ответ только обновляет показанное.
 *
 * `asOf` (`T-M2-31`) - дата остатка в виде `YYYY-MM-DD`, как её отдаёт `dayjs.format`; `null` -
 * параметр не отправляется, сервер берёт «сегодня» (UTC). Дата входит в КЛЮЧ запроса: смена даты -
 * новый запрос, а вернувшись к уже виденной дате, страница получает её из кэша и перечитывает
 * (второй пишущий `GET`, безвредный по тому же свойству `accrue_leave`). Сервер применяет `as_of`
 * ТОЛЬКО как фильтр показа: журнал по ней не меняется, начисление идёт до сегодняшнего дня. */
export function useMyBalancesQuery(employeeId: number | null, asOf: string | null = null) {
  return useQuery({
    queryKey: ["balances", employeeId, asOf],
    enabled: employeeId !== null,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    queryFn: async () => {
      if (employeeId === null) {
        throw new Error("GET /api/employees/{id}/balances: нет карточки сотрудника");
      }
      const { data, error, response } = await apiClient.GET("/api/employees/{employee_id}/balances", {
        params: {
          path: { employee_id: employeeId },
          query: asOf === null ? {} : { as_of: asOf },
        },
      });
      if (!data) {
        throw new BalancesLoadError(
          response.status,
          stringDetail((error as { detail?: unknown } | undefined)?.detail),
        );
      }
      return data;
    },
  });
}
