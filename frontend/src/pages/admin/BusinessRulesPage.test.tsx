import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { setAccessToken } from "../../app/auth";
import { BUSINESS_RULES_FIELD_COUNT } from "../../entities/businessRules";
import { setFetchHandler } from "../../test-utils/fetchMock";
import { businessRuleResponse, meResponse } from "../../test-utils/fixtures";
import { renderApp } from "../../test-utils/renderApp";

const ANNUAL = businessRuleResponse({
  key: "annual_leave_days",
  value: "28",
  updated_by_name: "Иванов Иван",
  updated_at: "2026-10-20T09:30:00",
});
const DEADLINE = businessRuleResponse({
  key: "schedule_approval_deadline",
  value: "12-17",
  value_type: "month_day",
  description: "Крайний срок утверждения графика отпусков на следующий год (месяц-день).",
  minimum: null,
  maximum: null,
  updated_at: "2026-01-05T00:00:00",
  updated_by_name: null,
});
const PASSWORD = businessRuleResponse({
  key: "password_min_length",
  value: "8",
  description: "Минимальная длина пароля.",
  minimum: 8,
  maximum: 72,
});
const REGISTRY = [ANNUAL, DEADLINE, PASSWORD];

type Reply = { status: number; body?: unknown };

/** Обращения к серверу за прогон теста: чтения реестра и записи. */
const reads = { count: 0 };

function mockBackend(role: string, rules: unknown[], onPut?: (values: Record<string, string>) => Reply) {
  setAccessToken("token");
  reads.count = 0;
  const writes: { method: string; pathname: string; values: Record<string, string> }[] = [];
  setFetchHandler((method, pathname, init) => {
    if (method === "GET" && pathname === "/api/auth/me") {
      return { status: 200, body: meResponse({ role, must_change_password: false }) };
    }
    if (method === "GET" && pathname === "/api/admin/business-rules") {
      reads.count += 1;
      return { status: 200, body: rules };
    }
    if (pathname === "/api/admin/business-rules" && method === "PUT") {
      const values = (JSON.parse(init.body as string) as { values: Record<string, string> }).values;
      writes.push({ method, pathname, values });
      return onPut?.(values) ?? null;
    }
    return null;
  });
  return writes;
}

/** Поле ищется по РУССКОЙ подписи (`description` из ответа), как его ищет пользователь. */
const LABEL: Record<string, string> = Object.fromEntries(REGISTRY.map((rule) => [rule.key, rule.description]));
const input = (key: string) => screen.getByLabelText(LABEL[key]) as HTMLInputElement;

/** Кнопка сохранения; пока запрос идёт, у неё в имени ещё «loading». */
const saveButton = () => screen.getByRole("button", { name: /Сохранить$/ });

/** Подписи ошибок, которые на экране сейчас: antd убирает снятую ошибку с анимацией, и она ещё
 * какое-то время остаётся в разметке с классом `-leave`. */
const visibleErrors = (root: ParentNode = document) =>
  Array.from(root.querySelectorAll(".ant-form-item-explain-error")).filter(
    (node) => !node.className.includes("-leave"),
  );

describe("business rules section (T-M2-15, FR-ADMIN-004)", () => {
  it("FR-ADMIN-004/1: shows every parameter in the server's order with its description, range and who changed it", async () => {
    mockBackend("hr_admin", REGISTRY);

    renderApp("/admin/business-rules");

    await screen.findByRole("heading", { name: "Администрирование → Параметры бизнес-правил" });
    const fields = screen.getAllByRole("textbox").map((node) => (node as HTMLInputElement).id);
    expect(fields).toEqual(["annual_leave_days", "schedule_approval_deadline", "password_min_length"]);
    expect(input("annual_leave_days").value).toBe("28");
    expect(input("schedule_approval_deadline").value).toBe("12-17");
    expect(screen.getByText(ANNUAL.description)).toBeInTheDocument();
    expect(screen.getByText(DEADLINE.description)).toBeInTheDocument();
    // Границы - только те, что прислал сервер; у `month_day` их нет.
    expect(screen.getByText("От 1 до 366")).toBeInTheDocument();
    expect(screen.getByText("От 8 до 72")).toBeInTheDocument();
    expect(screen.getAllByText(/^От \d+ до \d+$/)).toHaveLength(2);
    // Автор и время: имя из ответа; `null` - автор не указан.
    expect(screen.getByText("Изменён: 20.10.2026 09:30, Иванов Иван")).toBeInTheDocument();
    expect(screen.getByText("Обновлён: 05.01.2026 00:00, автор не указан")).toBeInTheDocument();
  });

  it("labels the fields in Russian (the description from the response) and still shows the key under each field", async () => {
    mockBackend("hr_admin", REGISTRY);

    renderApp("/admin/business-rules");

    await screen.findByLabelText(ANNUAL.description);
    for (const rule of REGISTRY) {
      const item = input(rule.key).closest(".ant-form-item") as HTMLElement;
      // Подпись - description, и только она: под полем тот же текст не повторяется.
      expect(within(item).getAllByText(rule.description)).toHaveLength(1);
      expect(item.querySelector("label")).toHaveTextContent(rule.description);
      // Ключ - отдельной строкой под полем, в <code>; английского идентификатора в подписи нет.
      expect(item.querySelector("label")).not.toHaveTextContent(rule.key);
      expect(within(item).getByText(rule.key).tagName).toBe("CODE");
    }
  });

  it("FR-ADMIN-004/1: saving sends ONLY the changed pairs", async () => {
    const writes = mockBackend("hr_admin", REGISTRY, () => ({
      status: 200,
      body: [businessRuleResponse({ ...ANNUAL, value: "30", updated_by_name: "Петрова Анна" }), DEADLINE, PASSWORD],
    }));
    const user = userEvent.setup();

    renderApp("/admin/business-rules");
    await screen.findByLabelText(LABEL.annual_leave_days);
    expect(saveButton()).toBeDisabled();

    await user.clear(input("annual_leave_days"));
    await user.type(input("annual_leave_days"), "30");
    await user.click(saveButton());

    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].values).toEqual({ annual_leave_days: "30" });
    expect(await screen.findByText("Параметры сохранены.")).toBeInTheDocument();
    expect(screen.getByText(/Изменён: .*, Петрова Анна/)).toBeInTheDocument();
    await waitFor(() => expect(saveButton()).toBeDisabled());
  });

  it("shows the CANONICAL form from the response, not what was typed ('029' is stored as '29')", async () => {
    const writes = mockBackend("hr_admin", REGISTRY, () => ({
      status: 200,
      body: [businessRuleResponse({ ...ANNUAL, value: "29" }), DEADLINE, PASSWORD],
    }));
    const user = userEvent.setup();

    renderApp("/admin/business-rules");
    await screen.findByLabelText(LABEL.annual_leave_days);
    await user.clear(input("annual_leave_days"));
    await user.type(input("annual_leave_days"), "029");
    await user.click(saveButton());

    await waitFor(() => expect(input("annual_leave_days").value).toBe("29"));
    expect(writes[0].values).toEqual({ annual_leave_days: "029" });
    // Поле и кэш сошлись: изменений относительно сохранённого нет.
    await waitFor(() => expect(saveButton()).toBeDisabled());
  });

  it("a 400 with two bad keys puts one caption under each of the two fields, the rest stay clean, nothing is saved", async () => {
    const writes = mockBackend("hr_admin", REGISTRY, () => ({
      status: 400,
      body: {
        detail:
          "annual_leave_days: значение недопустимо, нужно целое число от 1 до 366; " +
          "schedule_approval_deadline: значение недопустимо, нужна дата в формате ММ-ДД (например, 12-17), без 29 февраля",
      },
    }));
    const user = userEvent.setup();

    renderApp("/admin/business-rules");
    await screen.findByLabelText(LABEL.annual_leave_days);
    await user.clear(input("annual_leave_days"));
    await user.type(input("annual_leave_days"), "300");
    await user.clear(input("schedule_approval_deadline"));
    await user.type(input("schedule_approval_deadline"), "13-45");
    await user.clear(input("password_min_length"));
    await user.type(input("password_min_length"), "12");
    await user.click(saveButton());

    await waitFor(() => expect(writes).toHaveLength(1));
    const itemOf = (key: string) => input(key).closest(".ant-form-item") as HTMLElement;
    expect(
      await within(itemOf("annual_leave_days")).findByText("значение недопустимо, нужно целое число от 1 до 366"),
    ).toBeInTheDocument();
    expect(
      within(itemOf("schedule_approval_deadline")).getByText(
        "значение недопустимо, нужна дата в формате ММ-ДД (например, 12-17), без 29 февраля",
      ),
    ).toBeInTheDocument();
    expect(visibleErrors(itemOf("password_min_length"))).toHaveLength(0);
    expect(visibleErrors()).toHaveLength(2);
    // Префикс ключа пользователю не показывается; общей ошибки и «Сохранено» нет, набранное цело.
    expect(screen.queryByText(/annual_leave_days:/)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("Параметры сохранены.")).not.toBeInTheDocument();
    expect(input("password_min_length").value).toBe("12");
    expect(screen.getByText("Изменён: 20.10.2026 09:30, Иванов Иван")).toBeInTheDocument();
  });

  it("checks an integer against the range FROM THE RESPONSE before sending and does not call the server", async () => {
    const writes = mockBackend("hr_admin", REGISTRY);
    const user = userEvent.setup();

    renderApp("/admin/business-rules");
    await screen.findByLabelText(LABEL.annual_leave_days);
    await user.clear(input("annual_leave_days"));
    await user.type(input("annual_leave_days"), "400");
    await user.click(saveButton());

    expect(await screen.findByText("От 1 до 366.")).toBeInTheDocument();
    expect(writes).toHaveLength(0);
  });

  it("does not check the month_day format itself: the typed value goes to the server as it is", async () => {
    const writes = mockBackend("hr_admin", REGISTRY, () => ({
      status: 400,
      body: { detail: "schedule_approval_deadline: значение недопустимо" },
    }));
    const user = userEvent.setup();

    renderApp("/admin/business-rules");
    await screen.findByLabelText(LABEL.schedule_approval_deadline);
    await user.clear(input("schedule_approval_deadline"));
    await user.type(input("schedule_approval_deadline"), "13-45");
    await user.click(saveButton());

    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].values).toEqual({ schedule_approval_deadline: "13-45" });
  });

  it("a 404 (unknown key; unreachable from the form) shows the server text as is and keeps what was typed", async () => {
    mockBackend("hr_admin", REGISTRY, () => ({
      status: 404,
      body: { detail: "Параметры не найдены: annual_leave_days" },
    }));
    const user = userEvent.setup();

    renderApp("/admin/business-rules");
    await screen.findByLabelText(LABEL.annual_leave_days);
    await user.clear(input("annual_leave_days"));
    await user.type(input("annual_leave_days"), "30");
    await user.click(saveButton());

    expect(await screen.findByText("Параметры не найдены: annual_leave_days")).toBeInTheDocument();
    expect(input("annual_leave_days").value).toBe("30");
  });

  it("a 422 puts every error under its own field by the last loc element, the rest is one general text (T-M2-28)", async () => {
    mockBackend("hr_admin", REGISTRY, () => ({
      status: 422,
      body: {
        detail: [
          { loc: ["body", "values", "annual_leave_days"], msg: "Значение должно быть строкой.", type: "string_type" },
          { loc: ["body", "values", "password_min_length"], msg: "Значение должно быть строкой.", type: "string_type" },
          { loc: ["body"], msg: "Тело запроса должно быть объектом.", type: "model_attributes_type" },
        ],
      },
    }));
    const user = userEvent.setup();

    renderApp("/admin/business-rules");
    await screen.findByLabelText(LABEL.annual_leave_days);
    await user.clear(input("annual_leave_days"));
    await user.type(input("annual_leave_days"), "30");
    await user.click(saveButton());

    const itemOf = (key: string) => input(key).closest(".ant-form-item") as HTMLElement;
    expect(
      await within(itemOf("annual_leave_days")).findByText("Значение должно быть строкой."),
    ).toBeInTheDocument();
    expect(within(itemOf("password_min_length")).getByText("Значение должно быть строкой.")).toBeInTheDocument();
    expect(visibleErrors(itemOf("schedule_approval_deadline"))).toHaveLength(0);
    // Ошибка на всём теле - общим текстом над кнопками, не под случайным полем; запасного текста нет.
    expect(screen.getByRole("alert")).toHaveTextContent("Тело запроса должно быть объектом.");
    expect(screen.queryByText("Не удалось сохранить параметры.")).not.toBeInTheDocument();
  });

  it("a 422 whose detail is a plain string is shown as the server sent it", async () => {
    mockBackend("hr_admin", REGISTRY, () => ({ status: 422, body: { detail: "что-то иное" } }));
    const user = userEvent.setup();

    renderApp("/admin/business-rules");
    await screen.findByLabelText(LABEL.annual_leave_days);
    await user.clear(input("annual_leave_days"));
    await user.type(input("annual_leave_days"), "30");
    await user.click(saveButton());

    expect(await screen.findByText("что-то иное")).toBeInTheDocument();
  });

  it("loading: a form skeleton of BUSINESS_RULES_FIELD_COUNT fields (the number is guarded by a backend test against the registry, not by the rendered form)", async () => {
    setAccessToken("token");
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/admin/business-rules") {
        return gate.then(() => ({ status: 200, body: REGISTRY }));
      }
      return null;
    });

    const { container } = renderApp("/admin/business-rules");
    await screen.findByRole("button", { name: "Выйти" });

    expect(container.querySelector('[data-skeleton="form"]')).not.toBeNull();
    expect(container.querySelectorAll("[data-skeleton-field]")).toHaveLength(BUSINESS_RULES_FIELD_COUNT);
    await act(async () => {
      release();
    });
    expect(await screen.findByLabelText(LABEL.annual_leave_days)).toBeInTheDocument();
  });

  it("empty: an empty registry", async () => {
    mockBackend("hr_admin", []);

    renderApp("/admin/business-rules");

    expect(await screen.findByText("Параметров нет")).toBeInTheDocument();
  });

  it("error: a retry button that loads the registry on the second try", async () => {
    let attempts = 0;
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/admin/business-rules") {
        attempts += 1;
        return attempts === 1 ? { status: 500, body: { detail: "internal" } } : { status: 200, body: REGISTRY };
      }
      return null;
    });

    renderApp("/admin/business-rules");

    fireEvent.click(await screen.findByRole("button", { name: "Повторить" }));
    expect(await screen.findByLabelText(LABEL.annual_leave_days)).toBeInTheDocument();
  });

  it.each(["hr_admin", "system_admin"])("shows the menu item to %s in the Administration group", async (role) => {
    mockBackend(role, REGISTRY);

    renderApp("/admin/business-rules");

    await screen.findByLabelText(LABEL.annual_leave_days);
    expect(screen.getByRole("menuitem", { name: "Параметры бизнес-правил" })).toBeInTheDocument();
  });

  it.each(["employee", "accountant"])(
    "no access for %s: no menu item, a direct URL is denied, and the server is asked for NOTHING (neither reads nor writes)",
    async (role) => {
      const writes = mockBackend(role, REGISTRY);

      renderApp("/admin/business-rules");

      expect(
        await screen.findByText("Доступ запрещён. Обратитесь к HR-администратору."),
      ).toBeInTheDocument();
      expect(screen.queryByRole("menuitem", { name: "Параметры бизнес-правил" })).not.toBeInTheDocument();
      expect(reads.count).toBe(0);
      expect(writes).toHaveLength(0);
    },
  );

  it("the system administrator still starts on LDAP, not on the new section (ui.md §2.3)", async () => {
    mockBackend("system_admin", REGISTRY);

    const { router } = renderApp("/");

    await waitFor(() => expect(router.state.location.pathname).toBe("/admin/ldap-settings"));
  });
});
