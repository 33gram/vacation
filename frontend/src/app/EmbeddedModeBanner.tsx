import { Alert } from "antd";

/** `ui.md` §4, «Режим внедрения без LDAP»: баннер вверху содержимого,
 * живёт только внутри оболочки (после входа — не на экране входа, см.
 * `ui.md` §4 правку `T-M1-18`), не закрывается — это состояние системы, а
 * не уведомление. Кому его показывать (`hr_admin`/`system_admin`) решает
 * вызывающий код (`AppShell`), не этот компонент. */
export function EmbeddedModeBanner({ visible }: { visible: boolean }) {
  if (!visible) {
    return null;
  }
  return (
    <Alert
      type="warning"
      banner
      showIcon
      closable={false}
      message="Режим внедрения без LDAP: подключение к корпоративному каталогу не подтверждено. Пользователи входят по локальному паролю."
    />
  );
}
