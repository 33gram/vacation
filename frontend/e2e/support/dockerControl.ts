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

const COMPOSE_ARGS = [
  "compose",
  "--env-file",
  "../.env",
  "-f",
  "../infra/docker-compose.yml",
  "-f",
  "../infra/docker-compose.e2e.yml",
];

export function stopOpenldap(): void {
  execFileSync("docker", [...COMPOSE_ARGS, "stop", "openldap"], { stdio: "pipe" });
}

// Поднимает контейнер и ждёт, что порт действительно принимает
// соединения, прежде чем вернуть управление - опрос реального признака
// готовности, тот же принцип, что в Makefile/CI (не фиксированный sleep).
export function startOpenldapAndWaitReady(timeoutMs = 30_000): void {
  // PR #171 review, пункт 2(б)/(в): временная метка - удалить после
  // выбора лечения по итогам измерения (см. тело коммита).
  const startedAt = Date.now();
  execFileSync("docker", [...COMPOSE_ARGS, "start", "openldap"], { stdio: "pipe" });

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
      return;
    } catch {
      // ещё не готов - короткая синхронная пауза и повтор.
      execFileSync("sleep", ["1"]);
    }
  }
  throw new Error(`openldap не ответил за ${timeoutMs} мс после перезапуска`);
}

function requireLdapAdminPassword(): string {
  const value = process.env.LDAP_ADMIN_PASSWORD;
  if (!value) {
    throw new Error("LDAP_ADMIN_PASSWORD не задан в окружении - см. .env.example");
  }
  return value;
}
