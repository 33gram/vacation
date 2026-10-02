import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api/client";

/** `GET /api/admin/ldap-settings` (`T-M1-21`, `FR-ADMIN-005`) — отвечает
 * `null`, если параметры ни разу не сохраняли (`ldap_settings_service.
 * get_ldap_settings` — обычный `SELECT ... LIMIT 1` на singleton-таблицу,
 * `null`, если строк нет), а не 404: тело ответа и так типизировано как
 * `LdapSettingsOut | None` в самой схеме бэкенда, `null` здесь —
 * ожидаемое успешное значение, не повод для состояния ошибки. */
export function useLdapSettingsQuery() {
  return useQuery({
    queryKey: ["ldap-settings"],
    queryFn: async () => {
      const { data, response } = await apiClient.GET("/api/admin/ldap-settings");
      if (data === undefined) {
        throw new Error(`GET /api/admin/ldap-settings failed with status ${response.status}`);
      }
      return data;
    },
  });
}
