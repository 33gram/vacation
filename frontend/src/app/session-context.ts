import { createContext } from "react";

import type { components } from "../api/generated/schema";

type MeResponse = components["schemas"]["MeResponse"];

// Заполняется один раз, в `SessionGate` (после успешного `GET /api/auth/
// me`), и читается ниже по дереву (`AppShell`, `RoleGuard`, страницы) через
// `useSession` (`./useSession.ts`). Сам объект контекста живёт в файле без
// JSX-экспортов — `react-refresh/only-export-components` предупреждает,
// если рядом с компонентом (`SessionContext.tsx`'s `SessionProvider`)
// экспортируется что-то ещё, даже сам контекст (PR #144 review).
export const SessionContext = createContext<MeResponse | null>(null);
