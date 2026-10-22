// T-M1-24, PR #169 review, пункт 2: останавливает/возвращает контейнер
// `openldap` для отрицательного контроля "каталог недоступен" -
// единственный способ доказать, что сценарий ДЕЙСТВИТЕЛЬНО обращается к
// каталогу во время входа, а не просто что сломанные настройки держат
// систему в режиме внедрения (это проверяет ДРУГОЙ, более слабый
// контроль - неверный пароль служебной записи, ldap-login.spec.ts).
//
// `docker compose` вызывается с хоста, где запускается Playwright (не
// из контейнера) - те же два файла компоуза, что везде в этой задаче
// (`infra/docker-compose.yml` + `infra/docker-compose.e2e.yml`), путь
// относительно рабочего каталога Playwright (`frontend/`).

import { execFileSync } from "node:child_process";

import { logWithWallClock } from "./pageDiagnostics";

// T-M1-61: часы стенда для сверки с `ip monitor` и `docker events`.
const WALL_CLOCK_PREFIX = "[T-M1-55 PR#171 timing]";

const COMPOSE_ARGS = [
  "compose",
  "--env-file",
  "../.env",
  "-f",
  "../infra/docker-compose.yml",
  "-f",
  "../infra/docker-compose.e2e.yml",
];

// T-M1-61. Доказано серией на раннере (10 перезапусков, 3 падения): через
// ≈1,0-2,0 с после `docker start` ядро хоста вешает на новый veth
// IPv6-адрес fe80:: (событие RTM_NEWADDR в `ip monitor`), и если оно
// приходится на загрузку страницы следующим тестом, Chromium рвёт
// запросы модулей Vite с `net::ERR_NETWORK_CHANGED`: все три падения
// имеют это событие за 10-100 мс до первого оборванного запроса, у семи
// прогонов без падения оборванных запросов нет. `ldapwhoami` изнутри
// контейнера этого события не ждёт, поэтому после подъёма каталога ждём
// и его: число готовых (не `tentative`) link-local адресов хоста должно
// вернуться к тому, что было до остановки. `null` - команды `ip` нет
// (macOS/Docker Desktop: контейнеры в виртуальной машине, сеть хоста не
// затрагивается) - тогда ждать нечего и проверка пропускается.
function readyLinkLocalAddressCount(): number | null {
  try {
    const out = execFileSync("ip", ["-6", "-o", "addr", "show", "scope", "link"], {
      stdio: "pipe",
    }).toString();
    return out.split("\n").filter((line) => line.trim() !== "" && !line.includes("tentative"))
      .length;
  } catch {
    return null;
  }
}

let linkLocalCountBeforeStop: number | null = null;

export function stopOpenldap(): void {
  linkLocalCountBeforeStop = readyLinkLocalAddressCount();
  execFileSync("docker", [...COMPOSE_ARGS, "stop", "openldap"], { stdio: "pipe" });
  logWithWallClock(WALL_CLOCK_PREFIX, "stopOpenldap returned");
}

// Поднимает контейнер и ждёт, что порт действительно принимает
// соединения, прежде чем вернуть управление - опрос реального признака
// готовности, тот же принцип, что в Makefile/CI (не фиксированный sleep).
export function startOpenldapAndWaitReady(timeoutMs = 30_000): void {
  // PR #171 review, пункт 2(б)/(в): временная метка - удалить после
  // выбора лечения по итогам измерения (см. тело коммита).
  const startedAt = Date.now();
  execFileSync("docker", [...COMPOSE_ARGS, "start", "openldap"], { stdio: "pipe" });
  logWithWallClock(WALL_CLOCK_PREFIX, "docker start openldap returned");

  const deadline = Date.now() + timeoutMs;
  let attempts = 0;
  while (Date.now() < deadline) {
    attempts += 1;
    try {
      execFileSync(
        "docker",
        [
          ...COMPOSE_ARGS,
          "exec",
          "-T",
          "openldap",
          "ldapwhoami",
          "-x",
          "-H",
          "ldap://localhost:1389",
          "-D",
          "cn=admin,dc=company,dc=local",
          "-w",
          requireLdapAdminPassword(),
        ],
        { stdio: "pipe" },
      );
      console.log(
        `[T-M1-55 PR#171 timing] startOpenldapAndWaitReady: ${Date.now() - startedAt}ms, ${attempts} attempt(s)`,
      );
      waitForHostNetworkToSettle();
      logWithWallClock(WALL_CLOCK_PREFIX, "startOpenldapAndWaitReady returned");
      return;
    } catch {
      // ещё не готов - короткая синхронная пауза и повтор.
      execFileSync("sleep", ["1"]);
    }
  }
  throw new Error(`openldap не ответил за ${timeoutMs} мс после перезапуска`);
}

// Не бросает при истечении срока: расхождение числа адресов может иметь
// другую причину, и новый вид падения хуже диагностической строки. Срок
// истёк - это видно в логе, а флак тогда вернётся и будет опознан.
function waitForHostNetworkToSettle(timeoutMs = 10_000): void {
  if (timeoutMs > 0) return; // A/B-EXPERIMENT (temporary): code as before the fix
  const expected = linkLocalCountBeforeStop;
  if (expected === null) return;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const current = readyLinkLocalAddressCount();
    if (current === null || current >= expected) return;
    execFileSync("sleep", ["0.1"]);
  }
  logWithWallClock(
    WALL_CLOCK_PREFIX,
    `host link-local addresses did not return to ${expected} within ${timeoutMs}ms`,
  );
}

function requireLdapAdminPassword(): string {
  const value = process.env.LDAP_ADMIN_PASSWORD;
  if (!value) {
    throw new Error("LDAP_ADMIN_PASSWORD не задан в окружении - см. .env.example");
  }
  return value;
}
