import { useNavigate } from "react-router-dom";

import { accessOf } from "./roles";
import { useSession } from "./useSession";

/** Доступ текущей сессии (`roles.ts`, `accessOf`). */
export function useAccess() {
  return accessOf(useSession());
}

/** Переход на стартовую страницу роли: кнопка «На доступную страницу» состояния «Нет доступа»
 * (`ui.md` §4). Одна реализация для `RoleGuard` и страниц, которые получают 403 от сервера. */
export function useGoHome(): () => void {
  const navigate = useNavigate();
  const { homePath } = useAccess();
  return () => navigate(homePath ?? "/", { replace: true });
}
