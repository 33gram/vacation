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

  it("a prefix of a field this form does not have: the text goes above the buttons without the prefix (PR #189 check, П-3)", async () => {
    mockBackend(() => ({
      status: 400,
      body: { detail: "code: Код вида отпуска после создания не меняется" },
    }));

    renderApp("/employees/new");
    const user = userEvent.setup();

    await fillRequiredFields(user);
    await user.click(screen.getByRole("button", { name: "Создать" }));

    const text = await screen.findByText("Код вида отпуска после создания не меняется");
    expect(text.closest(".ant-alert")).not.toBeNull();
    expect(screen.queryByText(/code:/)).not.toBeInTheDocument();
  });

  it("two prefixes of fields this form does not have: both are stripped, both texts are shown (PR #189 check, П-3)", async () => {
    mockBackend(() => ({
      status: 400,
      body: { detail: "is_active: Нельзя деактивировать; balance_tracked: Нельзя снять признак" },
    }));

    renderApp("/employees/new");
    const user = userEvent.setup();

    await fillRequiredFields(user);
    await user.click(screen.getByRole("button", { name: "Создать" }));

    const text = await screen.findByText("Нельзя деактивировать Нельзя снять признак");
    expect(text.closest(".ant-alert")).not.toBeNull();
    expect(screen.queryByText(/is_active:|balance_tracked:/)).not.toBeInTheDocument();
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

describe("422 validation errors (T-M2-28)", () => {
  it("lays ALL errors under their fields (initial_password -> the password field) and the whole-body error above the buttons", async () => {
    mockBackend(() => ({
      status: 422,
      body: {
        detail: [
          { loc: ["body", "email"], msg: "Значение должно быть строкой.", type: "string_type" },
          { loc: ["body", "position"], msg: "Слишком длинное значение: не более 200 символов.", type: "string_too_long" },
          { loc: ["body", "initial_password"], msg: "Обязательное поле.", type: "missing" },
          { loc: ["body"], msg: "Тело запроса должно быть объектом.", type: "model_attributes_type" },
        ],
      },
    }));

    renderApp("/employees/new");
    const user = userEvent.setup();

    await fillRequiredFields(user);
    await user.click(screen.getByRole("radio", { name: "Локальный пароль" }));
    await user.type(await screen.findByLabelText("Первичный пароль"), "StrongPass1!");
    await user.click(screen.getByRole("button", { name: "Создать" }));

    const under = (label: string) => screen.getByLabelText(label).closest(".ant-form-item");
    expect(under("E-mail")).toContainElement(await screen.findByText("Значение должно быть строкой."));
    expect(under("Должность")).toContainElement(
      screen.getByText("Слишком длинное значение: не более 200 символов."),
    );
    expect(under("Первичный пароль")).toContainElement(screen.getByText("Обязательное поле."));
    const general = screen.getByText("Тело запроса должно быть объектом.");
    expect(general.closest(".ant-form-item")).toBeNull();
    expect(screen.queryByText("Не удалось создать карточку.")).not.toBeInTheDocument();
  });
});
