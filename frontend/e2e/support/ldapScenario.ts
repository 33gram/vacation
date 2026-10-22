// T-M1-24: подготовка данных для сценария "вход по LDAP -> карточка
// сотрудника" через API, не через интерфейс (сценарий заявляет и
// проверяет именно вход и карточку, а не настройку каталога - через UI
// это сделало бы тест длиннее, медленнее и ломким по причинам, не
// относящимся к заявленному).
//
// Порядок подготовки - с учётом двух ловушек, названных в тексте
// задачи:
//   1. вход первичным администратором, логин/пароль ИЗ ОКРУЖЕНИЯ
//      (BOOTSTRAP_ADMIN_LOGIN/BOOTSTRAP_ADMIN_PASSWORD), не хардкод -
//      NFR-SEC-004;
//   2. СНАЧАЛА смена пароля - до погашения флага обязательной смены
//      гейт T-M1-46 пускает сессию только в /auth/me, /auth/password/
//      change, /auth/password-policy, /auth/logout; любой другой
//      запрос вернул бы 403;
//   3. сохранение параметров LDAP с `is_active: true` и паролем
//      служебной записи ИЗ ОКРУЖЕНИЯ (LDAP_ADMIN_PASSWORD) - без
//      активной записи система в режиме внедрения, и вход шёл бы
//      локально;
//   4. карточка сотрудника с email, совпадающим с `mail` пользователя
//      каталога (infra/openldap/seed/01-people.ldif), способом входа
//      LDAP.
//
// Эта функция вызывается из НЕСКОЛЬКИХ тестов одного файла
// (ldap-login.spec.ts: позитивный сценарий и отрицательный контроль) на
// ОДНУ и ту же БД - без сброса между тестами. Два следствия:
//   - учётная запись администратора будет уже СМЕНИЛА пароль ко второму
//     вызову - вход пробует и исходный bootstrap-пароль, и (если тот
//     вернул 401) ранее установленный этой же функцией новый, вместо
//     того чтобы считать "уже не bootstrap-пароль" ошибкой;
//   - карточка сотрудника с фиксированным email (должен совпасть с
//     LDAP-записью, его нельзя рандомизировать) будет уже СОЗДАНА -
//     создание при конфликте не считается ошибкой, вызывающая сторона
//     получает id существующей карточки через `GET /api/employees`.
// `department`/`personnel_number` рандомизированы (`Date.now()`) и в
// этой идемпотентности не нуждаются - уникальность `department.name` не
// требуется вовсе, а `personnel_number` каждый вызов получает новый.

import { WALL_CLOCK_PREFIX } from "./wallClock";

const BASE_URL = "http://localhost";

const ADMIN_LOGIN = requireEnv("BOOTSTRAP_ADMIN_LOGIN");
const ADMIN_BOOTSTRAP_PASSWORD = requireEnv("BOOTSTRAP_ADMIN_PASSWORD");
const LDAP_SERVICE_ACCOUNT_PASSWORD = requireEnv("LDAP_ADMIN_PASSWORD");

// Новый пароль администратора после обязательной смены - НЕ секрет
// bootstrap-учётной записи (тот уже прочитан из окружения выше), а
// свежее значение, которое задаёт сам этот сценарий, - той же природы,
// что и синтетический каталожный пароль в DIRECTORY_USER ниже. Не
// содержит ADMIN_LOGIN ("admin" по умолчанию) ни в каком регистре -
// парольная политика запрещает логин подстрокой пароля.
const NEW_ADMIN_PASSWORD = "LdapE2eRun1!";

export const DIRECTORY_USER = {
  // infra/openldap/seed/01-people.ldif - синтетические данные тестового
  // каталога, лежащие в репозитории с M0; уместны и здесь по той же
  // причине.
  email: "ivanov@company.local",
  password: "TestPass123!",
  fullName: "Иван Иванов",
} as const;

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `${name} не задан в окружении - см. .env.example; e2e-сценарий читает ` +
        `секреты из окружения, а не хардкодит их (NFR-SEC-004)`,
    );
  }
  return value;
}

interface ApiResult<T> {
  status: number;
  body: T;
}

async function api<T>(
  path: string,
  init: { method: string; token?: string; body?: unknown } = { method: "GET" },
): Promise<ApiResult<T>> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (init.token) {
    headers.Authorization = `Bearer ${init.token}`;
  }
  const response = await fetch(`${BASE_URL}${path}`, {
    method: init.method,
    headers,
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  const text = await response.text();
  const body = (text ? JSON.parse(text) : undefined) as T;
  return { status: response.status, body };
}

interface LoginResponseBody {
  access_token: string;
  must_change_password: boolean;
}

async function loginAsAdmin(): Promise<string> {
  let login = await api<LoginResponseBody>("/api/auth/login", {
    method: "POST",
    body: { login: ADMIN_LOGIN, password: ADMIN_BOOTSTRAP_PASSWORD },
  });

  if (login.status === 401) {
    // Пароль уже сменён более ранним вызовом этой функции в той же БД
    // (другой тест этого же файла) - не ошибка, вход тем же новым
    // паролем.
    login = await api<LoginResponseBody>("/api/auth/login", {
      method: "POST",
      body: { login: ADMIN_LOGIN, password: NEW_ADMIN_PASSWORD },
    });
  }

  if (login.status !== 200) {
    throw new Error(
      `Не удалось войти администратором ни bootstrap-паролем, ни новым: ${login.status}`,
    );
  }

  const token = login.body.access_token;

  if (login.body.must_change_password) {
    const changed = await api("/api/auth/password/change", {
      method: "POST",
      token,
      body: { current_password: ADMIN_BOOTSTRAP_PASSWORD, new_password: NEW_ADMIN_PASSWORD },
    });
    if (changed.status !== 204) {
      throw new Error(`Не удалось сменить пароль администратора: ${changed.status}`);
    }
  }

  return token;
}

interface EmployeeOutBody {
  id: number;
  email: string;
}

async function createOrReuseDirectoryEmployee(
  token: string,
  departmentId: number,
): Promise<number> {
  const created = await api<EmployeeOutBody>("/api/employees", {
    method: "POST",
    token,
    body: {
      full_name: DIRECTORY_USER.fullName,
      personnel_number: `E2E-${Date.now()}`,
      department_id: departmentId,
      position: "Руководитель отдела разработки",
      hire_date: "2024-01-15",
      manager_id: null,
      email: DIRECTORY_USER.email,
      work_schedule_type: "standard",
      auth_type: "ldap",
    },
  });
  if (created.status === 201) {
    return created.body.id;
  }

  // Карточка с этим email уже существует - создана более ранним вызовом
  // этой функции в той же БД (другой тест этого же файла), не ошибка.
  const existing = await api<EmployeeOutBody[]>("/api/employees", { method: "GET", token });
  const match = existing.body.find(
    (employee) => employee.email.toLowerCase() === DIRECTORY_USER.email.toLowerCase(),
  );
  if (!match) {
    throw new Error(
      `Не удалось создать карточку сотрудника (${created.status}), и такой карточки нет среди существующих`,
    );
  }
  return match.id;
}

export interface ScenarioSetupResult {
  adminToken: string;
  employeeId: number;
  ldapConnectionConfirmed: boolean;
}

// PR #171 review, пункт 2(б): временная замерная обвязка - раскладывает
// бюджет setupLdapScenario по шагам, не оценивает его. Удалить после
// того, как лечение по измерению выбрано (см. тело коммита).
async function timed<T>(label: string, fn: () => Promise<T>): Promise<T> {
  const startedAt = Date.now();
  try {
    return await fn();
  } finally {
    console.log(`${WALL_CLOCK_PREFIX} ${label}: ${Date.now() - startedAt}ms`);
  }
}

export async function setupLdapScenario(
  options: { ldapBindPassword?: string } = {},
): Promise<ScenarioSetupResult> {
  const bindPassword = options.ldapBindPassword ?? LDAP_SERVICE_ACCOUNT_PASSWORD;

  const token = await timed("loginAsAdmin", () => loginAsAdmin());

  const ldapSettings = await timed("PUT /ldap-settings", () =>
    api("/api/admin/ldap-settings", {
      method: "PUT",
      token,
      body: {
        server_url: "openldap",
        port: 1389,
        base_dn: "dc=company,dc=local",
        bind_dn: "cn=admin,dc=company,dc=local",
        bind_password: bindPassword,
        user_match_attribute: "mail",
        is_active: true,
      },
    }),
  );
  if (ldapSettings.status !== 200) {
    throw new Error(`Не удалось сохранить параметры LDAP: ${ldapSettings.status}`);
  }

  // Найдено прогоном (не из текста задачи): `PUT` одного сохранения
  // недостаточно, чтобы вход пошёл через LDAP, несмотря на формулировку
  // "пока активной записи нет, система в режиме внедрения". Настоящее
  // условие - `is_embedded_mode` (embedded_mode.py) требует ЕЩЁ и
  // `last_test_status == "success"`, а `upsert_ldap_settings` каждое
  // сохранение сбрасывает его в NULL. Без этого вызова `POST /api/auth/
  // login` для сотрудника с `auth_type = "ldap"` диспетчеризовался бы на
  // ЛОКАЛЬНЫЙ вход (пустой хэш пароля -> 401), даже не пытаясь обратиться
  // к каталогу - воспроизведено прогоном при разработке этого сценария,
  // см. тело коммита. Пустое тело - проверка именно СОХРАНЁННЫХ
  // параметров (ldap_settings_service.test_ldap_connection), а не
  // черновых.
  const ldapTest = await timed("POST /ldap-settings/test", () =>
    api<{ success: boolean; detail: string }>("/api/admin/ldap-settings/test", {
      method: "POST",
      token,
      body: {},
    }),
  );
  const ldapConnectionConfirmed = ldapTest.status === 200 && ldapTest.body.success === true;

  const department = await timed("POST /departments", () =>
    api<{ id: number }>("/api/departments", {
      method: "POST",
      token,
      body: { name: `E2E Отдел ${Date.now()}` },
    }),
  );
  if (department.status !== 201) {
    throw new Error(`Не удалось создать подразделение: ${department.status}`);
  }

  const employeeId = await timed("createOrReuseDirectoryEmployee", () =>
    createOrReuseDirectoryEmployee(token, department.body.id),
  );

  return { adminToken: token, employeeId, ldapConnectionConfirmed };
}
