import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { getAccessToken } from "../app/auth";
import { setFetchHandler } from "../test-utils/fetchMock";
import { meResponse } from "../test-utils/fixtures";
import { renderApp } from "../test-utils/renderApp";

// PR #144 review: ни один из девяти тестов на момент ревью не трогал сам
// экран входа (все сажали сессию напрямую, `setAccessToken` + `renderApp`) —
// первый провал самой формы (валидатор `type="email"`, отклонявший
// служебный логин) остался бы незамеченным до ручной проверки.
describe("LoginPage (PR #144 review)", () => {
  it("logs in with a service login, not an e-mail — must fail if the field goes back to type=\"email\"", async () => {
    let loginRequestBody: unknown = null;
    setFetchHandler((method, pathname, init) => {
      if (method === "POST" && pathname === "/api/auth/login") {
        loginRequestBody = init.body ? JSON.parse(init.body as string) : null;
        return { status: 200, body: { access_token: "token-admin" } };
      }
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "system_admin", must_change_password: false }) };
      }
      return null;
    });

    renderApp("/login");
    const user = userEvent.setup();

    // "admin" — bootstrap-администратор (FR-AUTH-005), единственная запись
    // при первом запуске системы, без карточки сотрудника и без e-mail.
    // AntD не отправит `onFinish` при клиентской ошибке валидации — если
    // `type="email"` вернётся, этот `await` на кнопку "Войти" не дождётся
    // её исчезновения, и тест упадёт по таймауту, а не на assert ниже.
    await user.type(screen.getByLabelText("Логин или e-mail"), "admin");
    await user.type(screen.getByLabelText("Пароль"), "AdminPass1!");
    await user.click(screen.getByRole("button", { name: "Войти" }));

    expect(await screen.findByRole("button", { name: "Выйти" })).toBeInTheDocument();
    expect(loginRequestBody).toEqual({ login: "admin", password: "AdminPass1!" });
    expect(getAccessToken()).toBe("token-admin");
  });

  it("logs in with a corporate e-mail", async () => {
    let loginRequestBody: unknown = null;
    setFetchHandler((method, pathname, init) => {
      if (method === "POST" && pathname === "/api/auth/login") {
        loginRequestBody = init.body ? JSON.parse(init.body as string) : null;
        return { status: 200, body: { access_token: "token-hr" } };
      }
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
      }
      return null;
    });

    renderApp("/login");
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Логин или e-mail"), "ivanov@company.ru");
    await user.type(screen.getByLabelText("Пароль"), "IvanovPass1!");
    await user.click(screen.getByRole("button", { name: "Войти" }));

    expect(await screen.findByRole("button", { name: "Выйти" })).toBeInTheDocument();
    expect(loginRequestBody).toEqual({ login: "ivanov@company.ru", password: "IvanovPass1!" });
  });

  it("shows the server's text on a 401 and stays on the login screen", async () => {
    setFetchHandler((method, pathname) => {
      if (method === "POST" && pathname === "/api/auth/login") {
        return { status: 401, body: { detail: "Неверный логин или пароль" } };
      }
      return null;
    });

    renderApp("/login");
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Логин или e-mail"), "admin");
    await user.type(screen.getByLabelText("Пароль"), "WrongPassword1!");
    await user.click(screen.getByRole("button", { name: "Войти" }));

    expect(await screen.findByText("Неверный логин или пароль")).toBeInTheDocument();
    // Остаёмся на экране входа — не ушли ни в оболочку, ни куда-либо ещё.
    expect(screen.getByLabelText("Логин или e-mail")).toBeInTheDocument();
    expect(getAccessToken()).toBeNull();
  });

  it("sends must_change_password: true straight to the change-password screen", async () => {
    setFetchHandler((method, pathname) => {
      if (method === "POST" && pathname === "/api/auth/login") {
        return { status: 200, body: { access_token: "token-new-hire" } };
      }
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "employee", must_change_password: true }) };
      }
      return null;
    });

    renderApp("/login");
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Логин или e-mail"), "petrova");
    await user.type(screen.getByLabelText("Пароль"), "TempPass1!");
    await user.click(screen.getByRole("button", { name: "Войти" }));

    await waitFor(() => {
      expect(screen.getByText("Смена пароля")).toBeInTheDocument();
    });
  });
});
