// T-M1-61: общая обвязка журнала E2E - префиксы и печать с часами стенда.
// Метки ISO UTC сверяются с `ip monitor` и `docker events` (шаги job'а E2E
// в .github/workflows/ci.yml).

// Префикс замеров длительности шагов (пришёл из PR #171 review, T-M1-55).
export const WALL_CLOCK_PREFIX = "[T-M1-55 PR#171 timing]";

export function logWithWallClock(prefix: string, message: string): void {
  console.log(`${prefix} ${new Date().toISOString()} ${message}`);
}
