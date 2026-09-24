import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { clearAccessToken } from "./auth";

/** Общий выход — раньше `handleLogout` был продублирован в `AppShell` и
 * `ChangePasswordPage`, и ни одна из копий не трогала кэш React Query:
 * после выхода и входа под другой учётной записью в той же вкладке `["me"]`
 * ещё какое-то время отдавал бы ответ ПРЕЖНЕЙ сессии (react-query держит
 * неактивные запросы в кэше по умолчанию 5 минут после размонтирования
 * последнего наблюдателя, не удаляет сразу) — роль и меню отставали бы от
 * факта. `queryClient.clear()`, а не адресный `removeQueries({queryKey:
 * ["me"]})`: весь остальной кэш (`password-policy` и то, что появится на
 * будущих экранах) точно так же привязан к КОНКРЕТНОЙ учётной записи, и
 * адресный сброс означал бы держать в голове каждый новый ключ кэша на
 * каждом будущем экране — забыть про один воспроизвело бы тот же баг для
 * него одного. */
export function useLogout(): () => void {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  return () => {
    clearAccessToken();
    queryClient.clear();
    navigate("/login", { replace: true });
  };
}
