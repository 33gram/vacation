import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { setAccessToken } from "../../app/auth";
import { setFetchHandler } from "../../test-utils/fetchMock";
import { leaveTypeResponse, meResponse } from "../../test-utils/fixtures";
import { renderApp } from "../../test-utils/renderApp";

const ANNUAL = leaveTypeResponse({ id: 1, code: "annual_paid", name: "Основной оплачиваемый отпуск" });
const TIME_OFF = leaveTypeResponse({
  id: 2,
  code: "time_off",
  name: "Отгул",
  unit: "hours",
  reduces_main_balance: false,
  carry_over_policy: "none",
  annual_limit: "5.00",
});
const UNPAID = leaveTypeResponse({
  id: 3,
  code: "unpaid",
  name: "Отпуск без сохранения заработной платы",
  is_paid: "none",
  balance_tracked: false,
  is_active: false,
});

type Reply = { status: number; body?: unknown };

/** Число обращений к `GET /api/leave-types` за прогон теста. */
const reads = { count: 0 };

function mockBackend(
  role: string,
  leaveTypes: unknown[],
  onWrite?: (method: string, pathname: string, body: unknown) => Reply | null,
  listStatus = 200,
) {
  setAccessToken("token");
  reads.count = 0;
  const writes: { method: string; pathname: string; body: Record<string, unknown> }[] = [];
  setFetchHandler((method, pathname, init) => {
    if (method === "GET" && pathname === "/api/auth/me") {
      return { status: 200, body: meResponse({ role, must_change_password: false }) };
    }
    if (method === "GET" && pathname === "/api/leave-types") {
      reads.count += 1;
      return listStatus === 200
        ? { status: 200, body: leaveTypes }
        : { status: listStatus, body: { detail: "internal" } };
    }
    if ((method === "POST" || method === "PUT") && pathname.startsWith("/api/leave-types")) {
      const body = JSON.parse(init.body as string) as Record<string, unknown>;
      writes.push({ method, pathname, body });
      return onWrite?.(method, pathname, body) ?? null;
    }
    return null;
  });
  return writes;
}

const ALL_ATTRIBUTES = [
  "balance_tracked",
  "annual_limit",
  "carry_over_policy",
  "counted_non_working_days",
  "is_active",
  "is_paid",
  "name",
  "reduces_main_balance",
  "requires_approval",
  "requires_document",
  "unit",
].sort();

describe("leave types section (T-M2-14, FR-ADMIN-001)", () => {
  it("lists the directory with the code as the link to the record", async () => {
    mockBackend("hr_admin", [ANNUAL, TIME_OFF, UNPAID]);

    renderApp("/admin/leave-types");

    expect(await screen.findByRole("heading", { name: "Виды отсутствий (3)" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "time_off" })).toHaveAttribute("href", "/admin/leave-types/2");
    const unpaidRow = screen.getByText("Отпуск без сохранения заработной платы").closest("tr")!;
    expect(within(unpaidRow).getByText("Не оплачивается")).toBeInTheDocument();
    expect(within(screen.getByText("Отгул").closest("tr")!).getByText("Часы")).toBeInTheDocument();
  });

  it("empty: no leave types at all", async () => {
    mockBackend("hr_admin", []);

    renderApp("/admin/leave-types");

    expect(await screen.findByText("Видов отсутствий нет")).toBeInTheDocument();
  });

  it("error: a retry button that loads the list on the second try", async () => {
    let attempts = 0;
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/leave-types") {
        attempts += 1;
        return attempts === 1
          ? { status: 500, body: { detail: "internal" } }
          : { status: 200, body: [ANNUAL] };
      }
      return null;
    });

    renderApp("/admin/leave-types");

    fireEvent.click(await screen.findByRole("button", { name: "Повторить" }));
    expect(await screen.findByRole("heading", { name: "Виды отсутствий (1)" })).toBeInTheDocument();
  });

  it.each(["hr_admin", "system_admin"])("shows the menu item to %s in the Administration group", async (role) => {
    mockBackend(role, [ANNUAL]);

    renderApp("/admin/leave-types");

    await screen.findByRole("heading", { name: "Виды отсутствий (1)" });
    expect(screen.getByRole("menuitem", { name: "Виды отсутствий" })).toBeInTheDocument();
  });

  it.each(["employee", "accountant"])(
    "no access for %s: no menu item, a direct URL is denied, and the server is asked for NOTHING (neither reads nor writes)",
    async (role) => {
      const writes = mockBackend(role, [ANNUAL]);

      renderApp("/admin/leave-types");

      expect(
        await screen.findByText("Доступ запрещён. Обратитесь к HR-администратору."),
      ).toBeInTheDocument();
      expect(screen.queryByRole("menuitem", { name: "Виды отсутствий" })).not.toBeInTheDocument();
      expect(reads.count).toBe(0);
      expect(writes).toHaveLength(0);
    },
  );

  it("the system administrator still starts on LDAP, not on the new section (ui.md §2.3)", async () => {
    mockBackend("system_admin", [ANNUAL]);

    const { router } = renderApp("/");

    await waitFor(() => expect(router.state.location.pathname).toBe("/admin/ldap-settings"));
  });
});

describe("leave type card and form (FR-ADMIN-001/1: all attributes of section 6)", () => {
  it("FR-ADMIN-001/1: the card shows every attribute of the leave type", async () => {
    mockBackend("hr_admin", [ANNUAL, TIME_OFF]);

    renderApp("/admin/leave-types/2");

    expect(await screen.findByRole("heading", { name: "Отгул" })).toBeInTheDocument();
    for (const label of [
      "Код",
      "Название",
      "Оплачиваемость",
      "Единица учёта",
      "Годовой лимит",
      "Перенос неиспользованного остатка",
      "Уменьшает остаток основного отпуска",
      "По виду ведётся остаток",
      "Требует подтверждающий документ",
      "Требует согласования",
      "Выходные и праздники внутри периода",
      "Вид используется",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText("5.00")).toBeInTheDocument();
    expect(screen.getByText("Часы")).toBeInTheDocument();
  });

  it("FR-ADMIN-001/1: creating sends EVERY attribute of section 6, including the balance flag", async () => {
    const writes = mockBackend("hr_admin", [ANNUAL], () => ({
      status: 201,
      body: leaveTypeResponse({ id: 9, code: "comp_leave", name: "Компенсационный" }),
    }));
    const user = userEvent.setup();

    renderApp("/admin/leave-types/new");

    await user.type(await screen.findByLabelText("Код"), "comp_leave");
    await user.type(screen.getByLabelText("Название"), "Компенсационный");
    await user.click(screen.getByRole("switch", { name: "По виду ведётся остаток" }));
    await user.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].method).toBe("POST");
    expect(Object.keys(writes[0].body).sort()).toEqual([...ALL_ATTRIBUTES, "code"].sort());
    expect(writes[0].body).toMatchObject({ code: "comp_leave", name: "Компенсационный", balance_tracked: true });
  });

  it("FR-ADMIN-001/1: editing sends every attribute but the code, and the toggled one changed", async () => {
    const writes = mockBackend("hr_admin", [ANNUAL, TIME_OFF], (_method, _path, body) => ({
      status: 200,
      body: leaveTypeResponse({ id: 2, code: "time_off", name: "Отгул", ...(body as object) }),
    }));
    const user = userEvent.setup();

    renderApp("/admin/leave-types/2");
    await user.click(await screen.findByRole("button", { name: "Редактировать" }));
    expect(screen.getByLabelText("Код")).toBeDisabled();
    await user.click(screen.getByRole("switch", { name: "По виду ведётся остаток" }));
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]).toMatchObject({ method: "PUT", pathname: "/api/leave-types/2" });
    expect(Object.keys(writes[0].body).sort()).toEqual(ALL_ATTRIBUTES);
    expect(writes[0].body.balance_tracked).toBe(false);
    expect(writes[0].body.annual_limit).toBe("5.00");
  });
});

describe("the card takes its record from the list (there is no GET for one leave type)", () => {
  it("a direct URL of a non-existent leave type shows «not found» with a way back", async () => {
    mockBackend("hr_admin", [ANNUAL, TIME_OFF]);

    const { router } = renderApp("/admin/leave-types/999");

    expect(await screen.findByText("Вид не найден")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "К списку видов" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/admin/leave-types"));
  });

  it("a page reload on the card (mounting with no list loaded before) loads the list once and shows the card", async () => {
    mockBackend("hr_admin", [ANNUAL, TIME_OFF]);

    renderApp("/admin/leave-types/2");

    expect(await screen.findByRole("heading", { name: "Отгул" })).toBeInTheDocument();
    expect(reads.count).toBe(1);
  });
});

describe("API refusals look like refusals, with the server's own text (ui.md §7)", () => {
  const fieldItemOf = (label: string) => screen.getByLabelText(label).closest(".ant-form-item")!;

  it("duplicate code (POST 409): under the «Код» field", async () => {
    mockBackend("hr_admin", [ANNUAL], () => ({
      status: 409,
      body: { detail: "Вид отсутствий с кодом comp_leave уже существует" },
    }));
    const user = userEvent.setup();

    renderApp("/admin/leave-types/new");
    await user.type(await screen.findByLabelText("Код"), "comp_leave");
    await user.type(screen.getByLabelText("Название"), "Компенсационный");
    await user.click(screen.getByRole("button", { name: "Создать" }));

    const message = await screen.findByText("Вид отсутствий с кодом comp_leave уже существует");
    expect(fieldItemOf("Код")).toContainElement(message);
  });

  it("clearing «balance tracked» on a type with events (PUT 409): under that field", async () => {
    mockBackend("hr_admin", [ANNUAL, TIME_OFF], () => ({
      status: 409,
      body: { detail: "Нельзя снять признак «по виду ведётся остаток»: по виду уже есть события журнала" },
    }));
    const user = userEvent.setup();

    renderApp("/admin/leave-types/2");
    await user.click(await screen.findByRole("button", { name: "Редактировать" }));
    await user.click(screen.getByRole("switch", { name: "По виду ведётся остаток" }));
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    const message = await screen.findByText(/Нельзя снять признак/);
    expect(fieldItemOf("По виду ведётся остаток")).toContainElement(message);
  });

  it.each([
    "Код вида отсутствий менять нельзя",
    "Основной вид нельзя деактивировать или лишить признака «по виду ведётся остаток»: по нему начисляется отпуск",
  ])("PUT 400 «%s»: shown as is above the buttons, not as a crash", async (text) => {
    mockBackend("hr_admin", [ANNUAL, TIME_OFF], () => ({ status: 400, body: { detail: text } }));
    const user = userEvent.setup();

    renderApp("/admin/leave-types/1");
    await user.click(await screen.findByRole("button", { name: "Редактировать" }));
    await user.click(screen.getByRole("switch", { name: "Вид используется" }));
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    const alert = await screen.findByText(text);
    expect(alert.closest(".ant-alert")).not.toBeNull();
    // Форма осталась открытой и в ней нет ошибок под полями.
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeInTheDocument();
    expect(document.querySelector(".ant-form-item-explain-error")).toBeNull();
  });
});
