import { expect } from "@playwright/test";

import { startOpenldapAndWaitReady, stopOpenldap } from "./support/dockerControl";
import { DIRECTORY_USER, setupLdapScenario } from "./support/ldapScenario";
import { test } from "./support/pageDiagnostics";
import { WALL_CLOCK_PREFIX } from "./support/wallClock";

// T-M1-24. Три теста в ОДНОМ файле, в этом порядке, намеренно:
// `ldap_settings` — singleton-таблица (одна строка на всю систему,
// `ldap_settings_service.py`), и третий тест сохраняет её с заведомо
// неверным паролем служебной записи — если он выполнится раньше (или
// параллельно с) первых двух, они столкнулись бы с уже испорченными
// параметрами, которые сами не трогают. `playwright.config.ts`
// фиксирует `workers: 1` ради этого же порядка между файлами (здесь
// он единственный, где это важно) — тесты этого набора не независимы
// друг от друга, это сознательное решение, а не оплошность.

test.describe.configure({ mode: "serial" });

test("logs in via LDAP, and the admin sees the matching employee card", async ({ page }) => {
  const { adminToken, ldapConnectionConfirmed } = await setupLdapScenario();
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

  // Подтверждение входа. Карточку сотрудника эта же сессия сама дальше
  // не открывает - НЕ потому что такого пути нет вовсе (на уровне API
  // он есть, см. docs/tasks.md T-M1-24 за ссылкой на can_view_employee),
  // а потому что интерфейс M1 его не предоставляет: при создании через
  // API карточке назначается role="employee" (проверено прогоном
  // 2026-10-11 - во всём backend ровно три места строят `Account(...)`:
  // bootstrap.py, role="system_admin" для одноразовой bootstrap-записи;
  // employee_service.py и import_service.py, оба role="employee"
  // без параметра - свойство верно на сегодняшнюю поверхность API, не
  // навсегда), а `/employees/:id` на фронтенде доступен только
  // hr_admin/system_admin (roles.ts,
  // NON_MENU_ROUTES) - сотрудник видит свой раздел «Мой отпуск» (с
  // T-M2-13; до неё - состояние "Пусто", StubPage). Карточка проверяется
  // ниже сессией администратора - той же, что готовила данные (токен из
  // setupLdapScenario, без повторного входа через интерфейс, см. ниже).
  await expect(page.getByRole("button", { name: "Выйти" })).toBeVisible();
  // T-M2-13: у сотрудника с карточкой стартовая страница - «Мой отпуск» (`ui.md` §2.3), и
  // страница ЗАПОЛНЕНА: тот же вход, что проверял заглушку роли, - отдельного входа ради раздела
  // не заводится (E2E расширяется по одному случаю, T-M1-59/T-M1-61). Данные не готовятся.
  // Само значение остатка зависит от дня прогона (начисление по полным месяцам рабочего
  // года), поэтому проверяется форма: в блоке основного отпуска две величины с единицей
  // (остаток года и сумма по всем годам). Пустая страница или блок без чисел так не пройдут.
  // ЗАВИСИТ ОТ ДВУХ ВЕЩЕЙ: (1) вид «Основной оплачиваемый отпуск» засеян миграцией `0006` (без
  // него блока нет); (2) остаток ПОЛОЖИТЕЛЕН: регэксп не принимает отрицательное число, а при
  // нулевом начислении счёт совпадений, по замечанию ревью, стал бы другим (прогоном не
  // проверялось). Положительный остаток обеспечивает дата приёма из подготовки сценария
  // (`ldapScenario.ts`, `hire_date`). Смена любого из двух краснит этот шаг - так и задумано,
  // утверждение не ослаблено.
  await expect(page).toHaveURL(/\/my-leave$/);
  await expect(page.getByRole("heading", { name: "Мой отпуск" })).toBeVisible();
  const mainLeave = page.getByLabel("Основной оплачиваемый отпуск");
  await expect(mainLeave.getByText("Остаток за текущий рабочий год")).toBeVisible();
  await expect(mainLeave.getByText(/^\d+(,\d+)? кал\. дн\.$/)).toHaveCount(2);

  await page.getByRole("button", { name: "Выйти" }).click();
  await expect(page).toHaveURL(/\/login$/);

  // 2. Та же карточка, созданная в setupLdapScenario с тем же email, что
  // и у только что вошедшего каталожного пользователя - открыта сессией
  // администратора. Токен ПЕРЕИСПОЛЬЗУЕТСЯ из API-подготовки, а не
  // получается повторным входом через интерфейс (PR #169 review,
  // пункт 1): вход через форму вызывает `fill()` с паролем, а
  // `trace: "retain-on-failure"` (playwright.config.ts) публикует
  // параметры действий в trace.zip при падении - админский пароль
  // оказался бы в артефакте CI на 7 дней. Пароль каталожного пользователя
  // выше остаётся напечатанным намеренно (это и есть проверяемый
  // сценарий, см. playwright.config.ts) - но для администратора в этом
  // нет необходимости: сессия нужна только чтобы открыть карточку,
  // и `localStorage`-ключ, который приложение проверяет
  // (`TOKEN_STORAGE_KEY`, src/app/auth.ts), публичен по имени, а не по
  // секретности. Остаток, названный прямо, а не умолчанный (PR #169
  // review, второй круг): `page.evaluate((token) => …, adminToken)`
  // ниже отдаёт в трассировку СВОЙ аргумент тем же способом, каким
  // `fill()` отдавал пароль, - сам токен администратора там всё равно
  // присутствует. Это осознанно принятый, более узкий остаток, не
  // отсутствие остатка вовсе: токен живёт 60 минут
  // (`settings.jwt_access_token_ttl_minutes`), принадлежит одноразовой
  // тестовой учётной записи этого прогона и не даёт войти заново после
  // истечения срока, в отличие от пароля — многоразового секрета из
  // `.env`, которым можно было бы войти снова и снова, в любой момент
  // после попадания в артефакт.
  await page.evaluate((token) => {
    localStorage.setItem("vt_access_token", token);
  }, adminToken);
  await page.goto("/employees");
  await page.getByRole("link", { name: DIRECTORY_USER.fullName }).click();

  await expect(page.getByText(DIRECTORY_USER.fullName)).toBeVisible();
  await expect(page.getByText(DIRECTORY_USER.email)).toBeVisible();
});

test("отрицательный контроль: вход каталожным паролем падает, когда каталог недоступен", async ({
  page,
}) => {
  // T-M1-24, PR #169 review, пункт 2 - ГЛАВНЫЙ отрицательный контроль:
  // доказывает, что позитивный сценарий ДЕЙСТВИТЕЛЬНО обращается к
  // каталогу во время входа, а не только что сломанные настройки не
  // выводят систему из режима внедрения (тот контроль - ниже, второй
  // случай, он проверяет другое свойство и не заменяет этот). Настройки
  // сохранены и УСПЕШНО проверены (как в позитивном сценарии) - затем
  // сам каталог останавливается, и вход должен упасть именно на шаге
  // входа, обратившись к недоступному каталогу.
  const { ldapConnectionConfirmed } = await setupLdapScenario();
  expect(
    ldapConnectionConfirmed,
    "подключение должно быть подтверждено ДО отключения каталога",
  ).toBe(true);

  const stopped = stopOpenldap();
  try {
    await page.goto("/login");
    await page.getByLabel("Логин или e-mail").fill(DIRECTORY_USER.email);
    await page.getByLabel("Пароль").fill(DIRECTORY_USER.password);

    const startedAt = Date.now();
    await page.getByRole("button", { name: "Войти" }).click();
    await expect(page.getByRole("button", { name: "Выйти" })).not.toBeVisible();
    const elapsedMs = Date.now() - startedAt;
    console.log(`[T-M1-24] login rejected ${elapsedMs}ms after click while openldap was stopped`);

    // Ревью измерило ~0.3с для этого отказа (ldap3 получает "connection
    // refused" от закрытого порта немедленно, не ждёт TCP-таймаута) -
    // зафиксировано как ожидание, чтобы будущее зависание (таймаут
    // подключения вместо быстрого отказа, например при смене закрытого
    // порта на "подвисший", недостижимый без reset) было заметно само
    // по себе, не потерялось в общем таймауте теста (30с).
    expect(elapsedMs, "отказ входа при недоступном каталоге должен быть быстрым").toBeLessThan(
      3000,
    );
  } finally {
    // Контейнер возвращается независимо от результата проверки выше -
    // иначе следующий тест (или следующий локальный прогон) получил бы
    // недоступный каталог не из-за собственной причины.
    startOpenldapAndWaitReady(stopped);
  }
});

test("отрицательный контроль: вход каталожным паролем падает, если сервисная учётная запись LDAP не может сама войти в каталог", async ({
  page,
}) => {
  // T-M1-24, пункт 8 исходной задачи - более слабый, вспомогательный
  // случай (см. комментарий у теста выше за тем, какой контроль главный
  // и почему): `POST /api/auth/login` диспетчеризует сотрудника с
  // `auth_type = local` на локальный вход ДАЖЕ при активном LDAP
  // (`local_service.uses_local_auth`), и если карточка была бы создана
  // локальным способом входа с совпадающим паролем, тот же сценарий
  // прошёл бы, не коснувшись каталога вовсе.
  //
  // Механизм здесь - не живой провал bind'а ВО ВРЕМЯ входа (это
  // проверяет тест выше, остановкой самого каталога): неверный пароль
  // служебной записи роняет саму проверку подключения (`POST
  // /ldap-settings/test`, `setupLdapScenario`, см. её докстринг), и
  // система остаётся в режиме внедрения (`is_embedded_mode` требует
  // `last_test_status == "success"`, embedded_mode.py) -
  // `/api/auth/login` для Ивана диспетчеризуется ЛОКАЛЬНО (нет хэша
  // пароля -> отказ), даже не пытаясь обратиться к каталогу напрямую.
  // Наблюдаемый результат тот же (вход отказывает), но то, что ДОКАЗЫВАЕТ
  // именно этот контроль, у́же: что сломанные учётные данные службы не
  // дают системе выйти из режима внедрения - не то, что сам вход
  // обращается к каталогу (для этого см. тест выше).
  // PR #171 review, пункт 2(б): временные метки времени - удалить после
  // выбора лечения по итогам измерения (см. тело коммита).
  const scenarioStartedAt = Date.now();
  const { ldapConnectionConfirmed } = await setupLdapScenario({
    ldapBindPassword: "definitely-wrong-service-account-password",
  });
  console.log(`${WALL_CLOCK_PREFIX} setupLdapScenario total: ${Date.now() - scenarioStartedAt}ms`);
  expect(ldapConnectionConfirmed, "неверный пароль должен был уронить проверку подключения").toBe(
    false,
  );

  const gotoStartedAt = Date.now();
  await page.goto("/login");
  console.log(`${WALL_CLOCK_PREFIX} page.goto: ${Date.now() - gotoStartedAt}ms`);
  const fillStartedAt = Date.now();
  await page.getByLabel("Логин или e-mail").fill(DIRECTORY_USER.email);
  console.log(`${WALL_CLOCK_PREFIX} fill login: ${Date.now() - fillStartedAt}ms`);
  await page.getByLabel("Пароль").fill(DIRECTORY_USER.password);
  await page.getByRole("button", { name: "Войти" }).click();

  // Остаёмся на экране входа с сообщением об ошибке - не ушли в
  // оболочку, как ушли бы при случайном локальном совпадении.
  await expect(page.getByLabel("Логин или e-mail")).toBeVisible();
  await expect(page.getByRole("button", { name: "Выйти" })).not.toBeVisible();
});
