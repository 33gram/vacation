import { Navigate } from "react-router-dom";

import { useAccess } from "./useAccess";
import { StubPage } from "../pages/StubPage";

/** Индекс-маршрут оболочки (`/`) — стартовая страница (`ui.md` §2.3): роль и НАЛИЧИЕ
 * карточки. Учётная запись без единого доступного раздела (`employee`/`accountant` без
 * карточки) получает страницу-заглушку прямо здесь, а не редирект в никуда. */
export function HomeRedirect() {
  const { homePath: target } = useAccess();

  if (!target) {
    return <StubPage />;
  }

  return <Navigate to={target} replace />;
}
