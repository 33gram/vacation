import { Typography } from "antd";

/** Раздел появится в `T-M1-21` — здесь только маршрут и guard (`T-M1-18`
 * отвечает за оболочку, не за содержимое разделов). */
export function LdapSettingsPage() {
  return <Typography.Title level={3}>Администрирование → LDAP</Typography.Title>;
}
