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
 *   - `enabled: employeeId > 0` - без карточки (`employee_id = null`) запрос не уходит сам по
 *     себе, а не потому что соседний компонент (`RoleGuard`) не пустил на страницу;
 *   - один запрос на МОНТИРОВАНИЕ: хук вызывается из одного места страницы, а два наблюдателя
 *     за одним ключом (React в режиме разработки монтирует компонент дважды) разделяют один
 *     запрос в полёте; перерисовка без размонтирования запроса не даёт.
 * Уход в другой раздел и возврат - это новое монтирование и ВТОРОЙ пишущий запрос. Вреда
 * нет: повторное начисление ничего не пишет (свойство `accrue_leave`, `T-M2-19`, проверено
 * backend-тестами, не этим файлом), поэтому второй ответ только обновляет показанное. */
export function useMyBalancesQuery(employeeId: number) {
  return useQuery({
    queryKey: ["balances", employeeId],
    enabled: employeeId > 0,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    queryFn: async () => {
      const { data, error, response } = await apiClient.GET("/api/employees/{employee_id}/balances", {
        params: { path: { employee_id: employeeId } },
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
