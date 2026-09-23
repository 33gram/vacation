import { Typography } from "antd";

/** Раздел появится в `T-M1-20` — здесь только маршрут и guard (`T-M1-18`
 * отвечает за оболочку, не за содержимое разделов). */
export function DepartmentsPage() {
  return <Typography.Title level={3}>Подразделения</Typography.Title>;
}
