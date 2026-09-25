import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api/client";

/** `GET /api/auth/password-policy` (`T-M1-45`) — общий для экрана смены
 * пароля (`ChangePasswordPage`, `T-M1-18`) и формы сотрудника с локальным
 * входом (`T-M1-19`, `PasswordPolicyChecklist`): оба места подсвечивают
 * чек-лист по кодам, пришедшим в 400 (`ErrorDetail.detail` — список
 * `{code, message}`), а не по своему расчёту. Один хук на оба места —
 * чтобы `queryKey` и форма ошибки не разошлись между копиями. В отдельном
 * от компонента файле — рядом с компонентом `react-refresh/only-export-
 * components` предупреждает на экспорт хука (тот же случай, что и
 * `SessionContext`/`useSession`, PR #144 review). */
export function usePasswordPolicyQuery() {
  return useQuery({
    queryKey: ["password-policy"],
    queryFn: async () => {
      const { data, response } = await apiClient.GET("/api/auth/password-policy");
      if (!data) {
        throw new Error(`GET /api/auth/password-policy failed with status ${response.status}`);
      }
      return data;
    },
    retry: false,
  });
}
