import { act, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { getAccessToken, setAccessToken } from "./auth";
import { setFetchHandler } from "../test-utils/fetchMock";
import { meResponse, PASSWORD_POLICY_HINTS } from "../test-utils/fixtures";
import { renderApp } from "../test-utils/renderApp";

describe("must_change_password gate (T-M1-18, FR-AUTH-002)", () => {
  it("returns any other route back to the change-password screen", async () => {
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: true }) };
      }
      return null;
    });

    const { router } = renderApp("/change-password");

    expect(await screen.findByText("Смена пароля")).toBeInTheDocument();

    // Попытка уйти на маршрут, который у hr_admin в остальном доступен
    // (изолирует именно гейт must_change_password от RoleGuard). Обёрнуто в
    // `act` — императивный `navigate()` вне обработчика события React иначе
    // не гарантирует, что вызванный им ре-рендер (редирект `SessionGate`
    // обратно) долетит до DOM раньше следующей проверки.
    await act(async () => {
      router.navigate("/employees");
    });

    expect(await screen.findByText("Смена пароля")).toBeInTheDocument();
    expect(screen.queryByText("Сотрудники")).not.toBeInTheDocument();
  });

  it("shows every violated checklist item after a 400, not only the first", async () => {
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: true }) };
      }
      if (method === "GET" && pathname === "/api/auth/password-policy") {
        return { status: 200, body: PASSWORD_POLICY_HINTS };
      }
      if (method === "POST" && pathname === "/api/auth/password/change") {
        return {
          status: 400,
          body: {
            detail: [
              { code: "too_short", message: "Пароль должен быть не короче 8 символов." },
              { code: "common_password", message: "Пароль входит в список распространённых." },
            ],
          },
        };
      }
      return null;
    });

    renderApp("/change-password");
    const user = userEvent.setup();

    await screen.findByText("Смена пароля");
    await user.type(screen.getByLabelText("Текущий пароль"), "OldPassword1!");
    await user.type(screen.getByLabelText("Новый пароль"), "weak");
    await user.click(screen.getByRole("button", { name: "Сменить пароль" }));

    // Оба нарушения из ответа — а третий пункт чек-листа, которого в
    // detail не было, остаётся не подсвеченным.
    expect(await screen.findByText(/не короче 8 символов.*не выполнено/)).toBeInTheDocument();
    expect(screen.getByText(/распространённых.*не выполнено/)).toBeInTheDocument();
    expect(screen.queryByText(/трёх типов из четырёх.*не выполнено/)).not.toBeInTheDocument();
  });
});

describe("401 handling (T-M1-18)", () => {
  it("clears storage and returns to the login screen on any 401", async () => {
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 401, body: { detail: "Invalid token" } };
      }
      return null;
    });

    renderApp("/");

    await waitFor(() => {
      expect(screen.getByLabelText("Логин или e-mail")).toBeInTheDocument();
    });
    expect(getAccessToken()).toBeNull();
  });

  it("password/change 401 with a dead session logs out, instead of showing a form error", async () => {
    setAccessToken("token");
    let meCallCount = 0;
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        meCallCount += 1;
        // Первый /me — обычная загрузка SessionGate (сессия ещё жива).
        // Второй — переспрос мидлвари после 401 от /password/change:
        // токен успел истечь, пока человек сидел на этом экране.
        return { status: meCallCount === 1 ? 200 : 401, body: meResponse({ role: "hr_admin", must_change_password: true }) };
      }
      if (method === "GET" && pathname === "/api/auth/password-policy") {
        return { status: 200, body: PASSWORD_POLICY_HINTS };
      }
      if (method === "POST" && pathname === "/api/auth/password/change") {
        return { status: 401, body: { detail: "Неверный текущий пароль" } };
      }
      return null;
    });

    renderApp("/change-password");
    const user = userEvent.setup();

    await screen.findByText("Смена пароля");
    await user.type(screen.getByLabelText("Текущий пароль"), "SomePassword1!");
    await user.type(screen.getByLabelText("Новый пароль"), "NewPassword1!");
    await user.click(screen.getByRole("button", { name: "Сменить пароль" }));

    await waitFor(() => {
      expect(screen.getByLabelText("Логин или e-mail")).toBeInTheDocument();
    });
    expect(getAccessToken()).toBeNull();
  });

  it("password/change 401 with a live session shows the server's text, without logging out", async () => {
    setAccessToken("token");
    let meCallCount = 0;
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        meCallCount += 1;
        // Обе проверки /me успешны — сессия всё это время жива, 401
        // от /password/change объясняется только неверным текущим паролем.
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: true }) };
      }
      if (method === "GET" && pathname === "/api/auth/password-policy") {
        return { status: 200, body: PASSWORD_POLICY_HINTS };
      }
      if (method === "POST" && pathname === "/api/auth/password/change") {
        return { status: 401, body: { detail: "Неверный текущий пароль" } };
      }
      return null;
    });

    renderApp("/change-password");
    const user = userEvent.setup();

    await screen.findByText("Смена пароля");
    await user.type(screen.getByLabelText("Текущий пароль"), "WrongPassword1!");
    await user.type(screen.getByLabelText("Новый пароль"), "NewPassword1!");
    await user.click(screen.getByRole("button", { name: "Сменить пароль" }));

    // T-M1-47: текст сервера показывается как есть (ui.md §7), уже
    // по-русски — мок здесь воспроизводит РЕАЛЬНЫЙ текст `auth/router.py`,
    // не произвольную строку.
    expect(await screen.findByText("Неверный текущий пароль")).toBeInTheDocument();
    expect(screen.getByText("Смена пароля")).toBeInTheDocument();
    expect(getAccessToken()).toBe("token");
    expect(meCallCount).toBeGreaterThanOrEqual(2);
  });
});

describe("menu composition by role (ui.md §2.1/§2.2)", () => {
  it("shows hr_admin their sections but not system_admin-only ones, and shows employee none at all", async () => {
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
      }
      return null;
    });

    renderApp("/");
    // "Сотрудники" — стартовая страница hr_admin (§2.3): пункт меню плюс то
    // же слово в хлебной крошке текущей страницы, поэтому здесь не
    // проверяется отдельно — остальные три пункта меню на "/employees" не
    // повторяются нигде, кроме сайдбара, и однозначно проверяют его состав.
    await screen.findByText("Подразделения");
    // hr_admin не получает ни "Администрирование → LDAP", ни
    // "Производственный календарь" — оба эндпоинта бэкенда
    // (`production_calendar_router.py`, `ldap_settings_router.py`)
    // требуют `system_admin`, только он. Прежде этот тест ожидал
    // обратное для календаря — `roles.ts` ошибочно открывал его и
    // `hr_admin`, хотя `GET`/`PUT` отвечали 403 (найдено и исправлено
    // при реализации `T-M1-22`).
    expect(screen.queryByText("LDAP")).not.toBeInTheDocument();
    expect(screen.queryByText("Производственный календарь")).not.toBeInTheDocument();

    // Второй рендер того же теста — не автоматически изолирован от первого
    // (`render()` из testing-library добавляет в тот же `document.body`,
    // авто-`cleanup` срабатывает только МЕЖДУ тестами): без явной очистки
    // здесь остался бы виден интерфейс hr_admin поверх которого рендерится
    // employee, и следующая проверка искала бы совсем не то, что нужно.
    cleanup();
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "employee", employee_id: null, must_change_password: false }) };
      }
      return null;
    });

    renderApp("/");
    await screen.findByText("Разделы этой роли ещё не готовы");

    expect(screen.queryByText("Сотрудники")).not.toBeInTheDocument();
    expect(screen.queryByText("Подразделения")).not.toBeInTheDocument();
    expect(screen.queryByText("LDAP")).not.toBeInTheDocument();
    expect(screen.queryByText("Производственный календарь")).not.toBeInTheDocument();
  });
});

describe("logout clears the session cache (PR #144 review)", () => {
  it("shows the NEW role's menu after logging out and back in as someone else, not a stale cached one", async () => {
    // T-M1-53 (PR #153 review): раньше здесь же проверялось
    // `queryClient.getQueryData(["me"]) === undefined` сразу после клика
    // «Выйти» — промежуточный момент между СИНХРОННЫМ `queryClient.clear()`
    // и ВОЗМОЖНЫМ авто-рефетчем ещё не отписавшегося `QueryObserver`
    // (`@tanstack/query-core`, `QueryObserver.setOptions` -> `#updateQuery`
    // -> `#executeFetch`) — у этих двух событий нет заданного порядка, и
    // при вытеснении под конкуренцией за CPU обгонял то один, то другой
    // (PR #151 это не устранило, T-M1-53 измерила: 3 из 6 прогонов на
    // двухъядерной машине).
    //
    // Тот же авто-рефетч, воспроизведено ЗДЕСЬ (детерминированно, без
    // нагрузки на CPU): двухшаговая замена обработчика (401 сразу после
    // клика, затем — гейт на /me новой сессии, после ввода логина) сама
    // была источником той же гонки на один такт раньше — рефетч ещё не
    // отписавшегося наблюдателя `["me"]` мог уйти С ПЕРВЫМ (ещё hr_admin)
    // обработчиком ДО того, как следующая строка теста успевала
    // подменить его на 401 (тот же порядок операций, что описан выше, и
    // с тем же отсутствием гарантии). Поэтому обработчик после «Выйти»
    // ставится ОДИН, сразу закрывающий весь остаток теста: до входа новым
    // пользователем — 401 (любой рефетч, в том числе от старого
    // наблюдателя, в любой момент), после — отложенный промис-гейт
    // (`meGate`). Ни один момент между логаутом и открытием гейта больше
    // не может увидеть hr_admin-ответ — второй обработчик для него просто
    // не существует.
    setAccessToken("token-a");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
      }
      return null;
    });

    renderApp("/");
    const user = userEvent.setup();

    await screen.findByText("Подразделения");

    const meGateResolver: { resolve: (() => void) | null } = { resolve: null };
    const meGate = new Promise<void>((resolve) => {
      meGateResolver.resolve = resolve;
    });
    let loggedInAsNewUser = false;
    setFetchHandler((method, pathname) => {
      if (method === "POST" && pathname === "/api/auth/login") {
        loggedInAsNewUser = true;
        return { status: 200, body: { access_token: "token-b" } };
      }
      if (method === "GET" && pathname === "/api/auth/me") {
        if (!loggedInAsNewUser) {
          return { status: 401, body: { detail: "Неверный логин или пароль" } };
        }
        return meGate.then(() => ({
          status: 200,
          body: meResponse({ role: "employee", employee_id: null, must_change_password: false }),
        }));
      }
      return null;
    });

    await user.click(screen.getByRole("button", { name: "Выйти" }));
    await screen.findByLabelText("Логин или e-mail");

    // employee — роль без единого пункта меню в M1 (`ui.md` §2.2), в отличие
    // от hr_admin выше.
    await user.type(screen.getByLabelText("Логин или e-mail"), "petrova2");
    await user.type(screen.getByLabelText("Пароль"), "PetrovaPass1!");
    await user.click(screen.getByRole("button", { name: "Войти" }));

    // Дожидаемся ухода СО страницы входа — сам переход маршрута
    // асинхронный (react-router), а /me новой сессии ещё НЕ отвечал
    // (гейт держится нарочно): если пункт меню hr_admin виден здесь, он
    // взялся не из сети (обработчик для hr_admin-ответа с логаута больше
    // не существует), а из непочищенного кэша прежней сессии — `SessionGate`
    // отдал бы его синхронно (stale-while-revalidate, кэш есть — до
    // какого-либо ответа сети).
    await waitFor(() => {
      expect(screen.queryByLabelText("Логин или e-mail")).not.toBeInTheDocument();
    });
    expect(screen.queryByText("Подразделения")).not.toBeInTheDocument();

    meGateResolver.resolve?.();
    await screen.findByText("Разделы этой роли ещё не готовы");
    expect(screen.queryByText("Подразделения")).not.toBeInTheDocument();
  });
});

describe("embedded-mode banner (ui.md §4)", () => {
  it("is not shown to employee, and is shown to hr_admin, for the same embedded_mode: true", async () => {
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return {
          status: 200,
          body: meResponse({ role: "employee", employee_id: null, must_change_password: false, embedded_mode: true }),
        };
      }
      return null;
    });

    renderApp("/");
    await screen.findByText("Разделы этой роли ещё не готовы");
    expect(screen.queryByText(/режим внедрения/i)).not.toBeInTheDocument();

    cleanup();
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return {
          status: 200,
          body: meResponse({ role: "hr_admin", must_change_password: false, embedded_mode: true }),
        };
      }
      return null;
    });

    renderApp("/");
    expect(await screen.findByText(/режим внедрения/i)).toBeInTheDocument();
  });
});
