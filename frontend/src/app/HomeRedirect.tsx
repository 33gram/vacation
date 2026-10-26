import { Navigate } from "react-router-dom";

import { asRole, defaultPathForRole } from "./roles";
import { useSession } from "./useSession";
import { StubPage } from "../pages/StubPage";

/** Индекс-маршрут оболочки (`/`) — стартовая страница (`ui.md` §2.3): роль и НАЛИЧИЕ
 * карточки. Учётная запись без единого доступного раздела (`employee`/`accountant` без
 * карточки) получает страницу-заглушку прямо здесь, а не редирект в никуда. */
export function HomeRedirect() {
  const me = useSession();
  const target = defaultPathForRole(asRole(me.role), me.employee_id !== null);

  if (!target) {
    return <StubPage />;
  }

  return <Navigate to={target} replace />;
}
