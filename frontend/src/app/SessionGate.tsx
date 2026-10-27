import { Navigate, Outlet, useLocation } from "react-router-dom";

import { accessOf } from "./roles";
import { useMe } from "./session";
import { SessionProvider } from "./SessionContext";
import { ErrorLoadingState, LoadingState } from "./states";

const CHANGE_PASSWORD_PATH = "/change-password";

/** Единственное место, где вызывается `GET /api/auth/me` (T-M1-18) и
 * решается, куда пускать дальше:
 * - «Загрузка»/«Ошибка загрузки» (`ui.md` §4) — пока сессия не подтверждена;
 * - `must_change_password = true` (FR-AUTH-002) — принудительно на экран
 *   смены пароля, с любого другого маршрута, флаг проверяется по ОТВЕТУ
 *   `/me`, а не только по телу ответа входа (страницу могут перезагрузить);
 * - иначе — обычная оболочка (`AppShell`) через дочерние маршруты.
 *
 * Рендерится ВНУТРИ `AuthGuard` — токен уже проверен, здесь проверяется
 * именно ДЕЙСТВУЮЩАЯ сессия. */
export function SessionGate() {
  const { data: me, isLoading, isError, refetch } = useMe();
  const location = useLocation();

  if (isLoading) {
    // ОЦЕНКА, не сверяется с итогом: закрывает ВСЁ приложение, а не раздел. «Реальные данные» -
    // первая страница после входа; какая она, зависит от роли, которую сессия ещё не назвала, и
    // оболочки с меню в этот момент нет вовсе. Поэтому минимальная форма - один блок.
    return <LoadingState shape={{ kind: "card", blocks: 1 }} />;
  }

  if (isError || !me) {
    return <ErrorLoadingState onRetry={() => refetch()} />;
  }

  const onChangePasswordRoute = location.pathname === CHANGE_PASSWORD_PATH;

  if (me.must_change_password && !onChangePasswordRoute) {
    return <Navigate to={CHANGE_PASSWORD_PATH} replace />;
  }

  if (!me.must_change_password && onChangePasswordRoute) {
    // Пароль уже сменён (например, вернулись назад после успешной смены,
    // или обновили страницу) — незачем оставаться на экране, которого
    // больше не требует состояние сессии.
    const target = accessOf(me).homePath ?? "/";
    return <Navigate to={target} replace />;
  }

  return (
    <SessionProvider value={me}>
      <Outlet />
    </SessionProvider>
  );
}
