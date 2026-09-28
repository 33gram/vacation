import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { setAccessToken } from "../app/auth";
import { setFetchHandler } from "../test-utils/fetchMock";
import { departmentResponse, meResponse, PASSWORD_POLICY_HINTS } from "../test-utils/fixtures";
import { renderApp } from "../test-utils/renderApp";

const DEPARTMENT = departmentResponse({ id: 1, name: "Отдел разработки" });

function mockBackend(handlePost: (body: Record<string, unknown>) => { status: number; body?: unknown }) {
  setAccessToken("token");
  setFetchHandler((method, pathname, init) => {
    if (method === "GET" && pathname === "/api/auth/me") {
      return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
    }
    if (method === "GET" && pathname === "/api/departments") {
      return { status: 200, body: [DEPARTMENT] };
    }
    if (method === "GET" && pathname === "/api/employees") {
      return { status: 200, body: [] };
    }
    if (method === "GET" && pathname === "/api/auth/password-policy") {
      return { status: 200, body: PASSWORD_POLICY_HINTS };
    }
    if (method === "POST" && pathname === "/api/employees") {
      const body = JSON.parse(init.body as string) as Record<string, unknown>;
      return handlePost(body);
    }
    return null;
  });
}

async function fillRequiredFields(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText("ФИО"), "Сидорова Анна Петровна");
  await user.type(screen.getByLabelText("Табельный номер"), "P-500");
  await user.click(screen.getByLabelText("Подразделение"));
  await user.click(await screen.findByTitle("Отдел разработки"));
  await user.type(screen.getByLabelText("Должность"), "Аналитик");
  await user.type(screen.getByLabelText("Дата приёма"), "15.01.2024{Enter}");
  await user.type(screen.getByLabelText("E-mail"), "sidorova@company.local");
}

describe("employee create form (T-M1-19, FR-EMP-001)", () => {
  it("shows the local-auth password checklist and highlights every violated code from a 400, not only the first", async () => {
    mockBackend(() => ({
      status: 400,
      body: {
        detail: [
          { code: "too_short", message: "Пароль должен быть не короче 8 символов." },
          { code: "common_password", message: "Пароль входит в список распространённых." },
        ],
      },
    }));

    renderApp("/employees/new");
    const user = userEvent.setup();

    await fillRequiredFields(user);
    await user.click(screen.getByRole("radio", { name: "Локальный пароль" }));
    await screen.findByText("Требования к паролю");
    await user.type(screen.getByLabelText("Первичный пароль"), "weak");
    await user.click(screen.getByRole("button", { name: "Создать" }));

    expect(await screen.findByText(/не короче 8 символов.*не выполнено/)).toBeInTheDocument();
    expect(screen.getByText(/распространённых.*не выполнено/)).toBeInTheDocument();
    // Отрицательный контроль: третий пункт чек-листа, не пришедший в
    // detail, не подсвечен.
    expect(screen.queryByText(/трёх типов из четырёх.*не выполнено/)).not.toBeInTheDocument();
  });

  it("places a field-prefixed server error under that field, without the prefix in the text", async () => {
    mockBackend(() => ({
      status: 400,
      body: { detail: "email: адрес уже используется другим сотрудником" },
    }));

    renderApp("/employees/new");
    const user = userEvent.setup();

    await fillRequiredFields(user);
    await user.click(screen.getByRole("button", { name: "Создать" }));

    const errorText = await screen.findByText("адрес уже используется другим сотрудником");
    const emailItem = screen.getByLabelText("E-mail").closest(".ant-form-item");
    expect(emailItem).toContainElement(errorText);
    // Отрицательный контроль: сам префикс пользователю не показан.
    expect(screen.queryByText(/^email:/)).not.toBeInTheDocument();
  });

  it("puts the typed password into initial_password when auth_type is local", async () => {
    let postBody: Record<string, unknown> | null = null;
    mockBackend((body) => {
      postBody = body;
      return { status: 201, body: { id: 42, ...body } };
    });

    renderApp("/employees/new");
    const user = userEvent.setup();

    await fillRequiredFields(user);
    await user.click(screen.getByRole("radio", { name: "Локальный пароль" }));
    await screen.findByText("Требования к паролю");
    await user.type(screen.getByLabelText("Первичный пароль"), "StrongPass1!");
    await user.click(screen.getByRole("button", { name: "Создать" }));

    await vi.waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ auth_type: "local", initial_password: "StrongPass1!" });
  });

  it("does not send an actual password value in initial_password when auth_type stays ldap", async () => {
    // Отрицательный контроль: если бы код когда-нибудь подставил сюда
    // реальный пароль вместо `null` (например, скопировав его из другого
    // поля), эта проверка должна упасть — сравнение именно со строкой,
    // а не просто `toBeFalsy()`, которое пропустило бы пустую строку.
    let postBody: Record<string, unknown> | null = null;
    mockBackend((body) => {
      postBody = body;
      return { status: 201, body: { id: 43, ...body } };
    });

    renderApp("/employees/new");
    const user = userEvent.setup();

    await fillRequiredFields(user);
    // Способ входа по умолчанию — LDAP (`EmployeeCreate.auth_type`), поле
    // пароля не трогаем — оно и не показано.
    expect(screen.queryByLabelText("Первичный пароль")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Создать" }));

    await vi.waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ auth_type: "ldap", initial_password: null });
  });
});
