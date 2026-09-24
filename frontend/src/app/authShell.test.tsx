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
        return { status: 401, body: { detail: "Invalid current password" } };
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
        return { status: 401, body: { detail: "Invalid current password" } };
      }
      return null;
    });

    renderApp("/change-password");
    const user = userEvent.setup();

    await screen.findByText("Смена пароля");
    await user.type(screen.getByLabelText("Текущий пароль"), "WrongPassword1!");
    await user.type(screen.getByLabelText("Новый пароль"), "NewPassword1!");
    await user.click(screen.getByRole("button", { name: "Сменить пароль" }));

    // T-M1-47 ещё не сделана — текст ожидаемо по-английски, показывается
    // как есть (ui.md §7).
    expect(await screen.findByText("Invalid current password")).toBeInTheDocument();
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
    expect(screen.getByText("Производственный календарь")).toBeInTheDocument();
    // hr_admin не получает "Администрирование → LDAP" — только system_admin.
    expect(screen.queryByText("LDAP")).not.toBeInTheDocument();

    // Второй рендер того же теста — не автоматически изолирован от первого
    // (`render()` из testing-library добавляет в тот же `document.body`,
    // авто-`cleanup` срабатывает только МЕЖДУ тестами): без явной очистки
    // здесь остался бы виден интерфейс hr_admin поверх которого рендерится
    // employee, и следующая проверка искала бы совсем не то, что нужно.
    cleanup();
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "employee", must_change_password: false }) };
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
    setAccessToken("token-a");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
      }
      return null;
    });

    const { queryClient } = renderApp("/");
    const user = userEvent.setup();

    await screen.findByText("Подразделения");

    await user.click(screen.getByRole("button", { name: "Выйти" }));
    await screen.findByLabelText("Логин или e-mail");

    // Проверка САМОГО механизма, не только его симптома: без
    // `queryClient.clear()` в `useLogout()` кэш `["me"]` пережил бы выход —
    // react-query держит неактивные запросы в кэше по умолчанию 5 минут
    // после размонтирования последнего наблюдателя, а не удаляет сразу по
    // размонтированию. Проверять это на составе меню НИЖЕ было бы
    // ненадёжно: react-query переспрашивает `/me` при повторном монтировании
    // компонента (`staleTime` по умолчанию — 0), и устаревшее меню могло бы
    // успеть замениться правильным раньше, чем успеет сработать любой ассерт.
    expect(queryClient.getQueryData(["me"])).toBeUndefined();

    setFetchHandler((method, pathname) => {
      if (method === "POST" && pathname === "/api/auth/login") {
        return { status: 200, body: { access_token: "token-b" } };
      }
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "employee", must_change_password: false }) };
      }
      return null;
    });

    // employee — роль без единого пункта меню в M1 (`ui.md` §2.2), в отличие
    // от hr_admin выше: если бы `["me"]` остался в кэше от прежней сессии,
    // здесь всё ещё было бы видно меню hr_admin вместо страницы-заглушки.
    await user.type(screen.getByLabelText("Логин или e-mail"), "petrova2");
    await user.type(screen.getByLabelText("Пароль"), "PetrovaPass1!");
    await user.click(screen.getByRole("button", { name: "Войти" }));

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
          body: meResponse({ role: "employee", must_change_password: false, embedded_mode: true }),
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
