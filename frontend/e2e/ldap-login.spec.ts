import { expect, test } from "@playwright/test";

import { DIRECTORY_USER, setupLdapScenario } from "./support/ldapScenario";

// T-M1-24. Два теста в ОДНОМ файле, в этом порядке, намеренно:
// `ldap_settings` — singleton-таблица (одна строка на всю систему,
// `ldap_settings_service.py`), и второй тест сохраняет её с заведомо
// неверным паролем служебной записи — если он выполнится первым (или
// параллельно), первый тест столкнулся бы с уже испорченными
// параметрами, которые он сам не трогает. `playwright.config.ts`
// фиксирует `workers: 1` ради этого же порядка между файлами (здесь
// он единственный, где это важно) — тесты этого набора не независимы
// друг от друга, это сознательное решение, а не оплошность.

test.describe.configure({ mode: "serial" });

test("logs in via LDAP, and the admin sees the matching employee card", async ({ page }) => {
  const { adminLogin, adminPassword, ldapConnectionConfirmed } = await setupLdapScenario();
  // Предусловие, не то, что проверяет этот тест: без успешной проверки
  // сохранённых параметров (`POST /ldap-settings/test`) система остаётся
  // в режиме внедрения (`is_embedded_mode`, `embedded_mode.py`) и вход
  // ниже диспетчеризовался бы локально, а не через LDAP, - упал бы по
  // другой причине (нет локального хэша пароля), маскируя настоящую.
  expect(ldapConnectionConfirmed, "проверка сохранённых параметров LDAP должна была пройти").toBe(
    true,
  );

  // 1. Вход пользователем каталога, его каталожным паролем.
  await page.goto("/login");
  await page.getByLabel("Логин или e-mail").fill(DIRECTORY_USER.email);
  await page.getByLabel("Пароль").fill(DIRECTORY_USER.password);
  await page.getByRole("button", { name: "Войти" }).click();

  // Подтверждение входа. Карточку сотрудника эта же сессия не может
  // открыть сама: role="employee" назначается ВСЕГДА при создании через
  // API (нет эндпоинта, устанавливающего другую роль), а `/employees/:id`
  // доступен только hr_admin/system_admin (roles.ts, NON_MENU_ROUTES) -
  // сотрудник без доступного в M1 раздела видит состояние "Пусто"
  // (StubPage). Это и есть узость, названная в docs/tasks.md (T-M1-24,
  // пункт 10): карточка проверяется ниже сессией администратора, не
  // сессией только что вошедшего сотрудника каталога.
  await expect(page.getByRole("button", { name: "Выйти" })).toBeVisible();
  await expect(page.getByText("Разделы этой роли ещё не готовы")).toBeVisible();

  await page.getByRole("button", { name: "Выйти" }).click();
  await expect(page).toHaveURL(/\/login$/);

  // 2. Та же карточка, созданная в setupLdapScenario с тем же email, что
  // и у только что вошедшего каталожного пользователя - открыта сессией
  // администратора (единственная роль, видящая /employees/:id в M1).
  await page.getByLabel("Логин или e-mail").fill(adminLogin);
  await page.getByLabel("Пароль").fill(adminPassword);
  await page.getByRole("button", { name: "Войти" }).click();

  // Ждём завершения входа ДО перехода на /employees - без этого `goto`
  // мог бы выполниться раньше, чем приложение получит и сохранит токен
  // (AuthGuard увидел бы отсутствие токена и увёл обратно на /login,
  // прежде чем асинхронный ответ POST /api/auth/login вообще пришёл бы -
  // воспроизведено прогоном, не предположено).
  await expect(page.getByRole("button", { name: "Выйти" })).toBeVisible();

  await page.goto("/employees");
  await page.getByRole("link", { name: DIRECTORY_USER.fullName }).click();

  await expect(page.getByText(DIRECTORY_USER.fullName)).toBeVisible();
  await expect(page.getByText(DIRECTORY_USER.email)).toBeVisible();

  await page.getByRole("button", { name: "Выйти" }).click();
});

test("отрицательный контроль: вход каталожным паролем падает, если сервисная учётная запись LDAP не может сама войти в каталог", async ({
  page,
}) => {
  // T-M1-24, пункт 8 — без этого контроля позитивный сценарий ничего не
  // доказывает: `POST /api/auth/login` диспетчеризует сотрудника с
  // `auth_type = local` на локальный вход ДАЖЕ при активном LDAP
  // (`local_service.uses_local_auth`), и если карточка была бы создана
  // локальным способом входа с совпадающим паролем, тот же сценарий
  // прошёл бы, не коснувшись каталога вовсе.
  //
  // Механизм здесь - не живой провал bind'а ВО ВРЕМЯ входа (как можно
  // было бы ожидать): неверный пароль служебной записи роняет саму
  // проверку подключения (`POST /ldap-settings/test`,
  // `setupLdapScenario`, см. её докстринг), и система остаётся в режиме
  // внедрения (`is_embedded_mode` требует `last_test_status ==
  // "success"`, embedded_mode.py) - `/api/auth/login` для Ивана
  // диспетчеризуется ЛОКАЛЬНО (нет хэша пароля -> отказ), даже не
  // пытаясь обратиться к каталогу напрямую. Наблюдаемый результат тот
  // же (вход отказывает), и то, что он доказывает, не меняется: ломая
  // ТОЛЬКО связь с каталогом (не саму карточку, не способ входа), вход
  // каталожным паролем перестаёт работать - значит позитивный сценарий
  // выше зависел именно от исправной связи с LDAP, а не от случайного
  // совпадения с локальным паролем.
  const { ldapConnectionConfirmed } = await setupLdapScenario({
    ldapBindPassword: "definitely-wrong-service-account-password",
  });
  expect(ldapConnectionConfirmed, "неверный пароль должен был уронить проверку подключения").toBe(
    false,
  );

  await page.goto("/login");
  await page.getByLabel("Логин или e-mail").fill(DIRECTORY_USER.email);
  await page.getByLabel("Пароль").fill(DIRECTORY_USER.password);
  await page.getByRole("button", { name: "Войти" }).click();

  // Остаёмся на экране входа с сообщением об ошибке - не ушли в
  // оболочку, как ушли бы при случайном локальном совпадении.
  await expect(page.getByLabel("Логин или e-mail")).toBeVisible();
  await expect(page.getByRole("button", { name: "Выйти" })).not.toBeVisible();
});
