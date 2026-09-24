import { Typography } from "antd";

/** Раздел появится в `T-M1-22` — здесь только маршрут и guard (`T-M1-18`
 * отвечает за оболочку, не за содержимое разделов). */
export function ProductionCalendarPage() {
  return (
    <Typography.Title level={3}>
      Администрирование → Производственный календарь
    </Typography.Title>
  );
}
