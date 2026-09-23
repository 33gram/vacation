import type { ReactElement } from "react";
import { useNavigate } from "react-router-dom";

import { asRole, defaultPathForRole, isPathAllowedForRole } from "./roles";
import { useSession } from "./SessionContext";
import { NoAccessState } from "./states";

/** Прямой переход на маршрут, не входящий в меню роли (`ui.md` §2.1: «в
 * запрещённый раздел из интерфейса не попасть» — меню его и не показывает,
 * но адресную строку это не останавливает) — состояние «Нет доступа к
 * записи» (`ui.md` §4). Текст — тот же, что вернул бы сервер для
 * `AccessDeniedError` (`main.py`, `FR-AUTH-004`): здесь до сервера дело не
 * доходит вовсе (проверка чисто клиентская, по составу меню), но текст —
 * тот же фиксированный системный, не изобретён заново. */
export function RoleGuard({
  path,
  children,
}: {
  path: string;
  children: ReactElement;
}): ReactElement {
  const me = useSession();
  const navigate = useNavigate();
  const role = asRole(me.role);

  if (!isPathAllowedForRole(role, path)) {
    const homePath = defaultPathForRole(role) ?? "/";
    return (
      <NoAccessState
        message="Доступ запрещён. Обратитесь к HR-администратору."
        onGoHome={() => navigate(homePath, { replace: true })}
      />
    );
  }

  return children;
}
