import { Navigate } from "react-router-dom";

import { asRole, defaultPathForRole } from "./roles";
import { useSession } from "./useSession";
import { StubPage } from "../pages/StubPage";

/** Индекс-маршрут оболочки (`/`) — стартовая страница по роли (`ui.md`
 * §2.3). Роль без доступного в M1 раздела (`employee`/`accountant`)
 * получает страницу-заглушку прямо здесь, а не редирект в никуда. */
export function HomeRedirect() {
  const me = useSession();
  const target = defaultPathForRole(asRole(me.role));

  if (!target) {
    return <StubPage />;
  }

  return <Navigate to={target} replace />;
}
