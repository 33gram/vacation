import { defineConfig, devices } from "@playwright/test";

// T-M1-24: приложение открывается через nginx (:80), не напрямую через
// Vite dev-сервер (:5173) — форма, близкая к прод-развёртыванию (`plan.md`
// §8), где перед собранной статикой и backend'ом тоже стоит nginx. Выбор
// сам по себе ничего не даёт без `infra/docker-compose.e2e.yml`: базовый
// `docker-compose.yml` задаёт `frontend`'у `VITE_API_BASE_URL: http://
// localhost:8000`, и Vite подставляет это значение в выданный браузеру
// бандл буквально — проверено прогоном (не рассуждением): `curl http://
// localhost/src/api/client.ts` через nginx отдавал строку с "localhost:
// 8000", то есть браузер слал бы запросы К API НАПРЯМУЮ, минуя nginx
// вовсе, и прокси-блок `/api/` (`infra/nginx/nginx.dev.conf`) в сценарии
// не участвовал бы — сценарий тестировал бы несуществующую развёртку.
// Оверрайд `docker-compose.e2e.yml` задаёт `VITE_API_BASE_URL: ""`
// (пустая строка — относительный путь, тот же origin, что и страница);
// после него тот же прогон отдавал буквально `"VITE_API_BASE_URL": ""`.
// Отклонённая альтернатива — :5173 напрямую с `VITE_API_BASE_URL=http://
// localhost:8000`: проще (оверрайд не нужен), но тестирует другую связку,
// не ту, что в проде (Vite dev-сервер вместо nginx, кросс-origin запрос
// к API вместо относительного пути через тот же origin).
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  // Один браузер — цель сценария не кроссбраузерность (T-M1-24).
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  use: {
    baseURL: "http://localhost",
    trace: "retain-on-failure",
  },
  // CI поднимает стек и ждёт готовности отдельным шагом (poll по реальному
  // признаку, не sleep, см. .github/workflows/ci.yml) до запуска этой
  // команды — `webServer` здесь не нужен и не был бы верным: Playwright
  // сам ничего не поднимает, только запускает тесты против уже
  // работающего стека.
  reporter: "list",
});
