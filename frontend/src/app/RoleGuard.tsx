import type { ReactElement } from "react";
import { isPathAllowedForRole } from "./roles";
import { NoAccessState } from "./states";
import { useAccess, useGoHome } from "./useAccess";

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
  const { role, hasEmployeeCard } = useAccess();
  const goHome = useGoHome();

  if (!isPathAllowedForRole(role, path, hasEmployeeCard)) {
    return (
      <NoAccessState
        message="Доступ запрещён. Обратитесь к HR-администратору."
        onGoHome={goHome}
      />
    );
  }

  return children;
}
