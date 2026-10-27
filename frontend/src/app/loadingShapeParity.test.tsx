import { act, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { daysInYear, isWeekendDate } from "../entities/productionCalendar";
import { setFetchHandler } from "../test-utils/fetchMock";
import {
  balanceEntryResponse,
  balancesResponse,
  departmentResponse,
  employeeResponse,
  ldapSettingsResponse,
  leaveTypeBalanceResponse,
  meResponse,
} from "../test-utils/fixtures";
import { renderApp } from "../test-utils/renderApp";
import { setAccessToken } from "./auth";

// `ui.md` §4: каркас «той же высоты, что и реальные данные». Высота в пикселях в jsdom не
// проверяется (нет раскладки); проверяется то, что можно: число строк/колонок/полей/блоков
// каркаса РАВНО числу, которое та же страница рисует после загрузки. Каркас и итог считаются в
// одном тесте рядом - это проверка смысла правила, а не формы вызова. Заодно проверяется сама
// ФОРМА каркаса (`data-skeleton` = table/card/form).
//
// ГРАНИЦА: итог считается по разметке antd (`.ant-form-item-label`, `.ant-descriptions`,
// `.ant-typography strong`, `tbody .ant-table-row`, `thead th`). Ломается она в БЕЗОПАСНУЮ
// сторону: поле без подписи или переименованный класс дают КРАСНЫЙ тест (счёт итога меняется,
// каркас - нет), а не тихо занижают обе стороны. Красный тест после правки разметки - это
// «не узнал поле», а не флак: поправить селектор или число, не гасить тест.

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

/** `/me` отвечает сразу, данные страницы - только после `gate`. */
function mockGated(role: string, responses: Record<string, unknown>) {
  const gate = deferred();
  setAccessToken("token");
  setFetchHandler((method, pathname) => {
    if (method === "GET" && pathname === "/api/auth/me") {
      return { status: 200, body: meResponse({ role, must_change_password: false }) };
    }
    if (method === "GET" && pathname in responses) {
      return gate.promise.then(() => ({ status: 200, body: responses[pathname] }));
    }
    return null;
  });
  return gate;
}

/** Дожидается оболочки: значит, `SessionGate` позади и каркас на экране - каркас страницы. */
async function waitForShell() {
  await screen.findByRole("button", { name: "Выйти" });
}

const countIn = (root: ParentNode, selector: string) => root.querySelectorAll(selector).length;

const skeletonKinds = (root: ParentNode) =>
  Array.from(root.querySelectorAll("[data-skeleton]")).map((node) =>
    node.getAttribute("data-skeleton"),
  );

describe("the loading skeleton has the shape of what the page draws after loading (ui.md §4)", () => {
  it("list (table): a full page of rows and as many columns as the table has", async () => {
    const employees = Array.from({ length: 12 }, (_, index) =>
      employeeResponse({
        id: index + 1,
        full_name: `Сотрудник ${index + 1}`,
        personnel_number: `P${index + 1}`,
        department_id: 1,
      }),
    );
    const gate = mockGated("hr_admin", {
      "/api/employees": employees,
      "/api/departments": [departmentResponse({ id: 1 })],
    });

    const { container } = renderApp("/employees");
    await waitForShell();
    const skeletonRows = container.querySelectorAll("[data-skeleton-row]");
    const skeletonColumns = skeletonRows[0]?.querySelectorAll("[data-skeleton-cell]").length;
    expect(skeletonKinds(container)).toEqual(["table"]);

    await act(async () => {
      gate.resolve();
    });
    await screen.findByRole("heading", { name: /Сотрудники/ });
    const renderedRows = countIn(container, "tbody .ant-table-row");
    const renderedColumns = countIn(container, "thead th");

    expect(skeletonRows.length).toBeGreaterThan(0);
    // 12 записей, страница выдачи - 10: на первой странице именно столько строк.
    expect(renderedRows).toBe(10);
    expect(skeletonRows.length).toBe(renderedRows);
    expect(skeletonColumns).toBe(renderedColumns);
  });

  it("settings form (LDAP): as many fields as the loaded form has", async () => {
    const gate = mockGated("system_admin", {
      "/api/admin/ldap-settings": ldapSettingsResponse(),
    });

    const { container } = renderApp("/admin/ldap-settings");
    await waitForShell();
    const skeletonFields = countIn(container, "[data-skeleton-field]");
    expect(skeletonKinds(container)).toEqual(["form"]);

    await act(async () => {
      gate.resolve();
    });
    await screen.findByRole("heading", { name: "Администрирование → LDAP" });
    const renderedFields = countIn(container, ".ant-form-item-label");

    expect(skeletonFields).toBeGreaterThan(0);
    expect(skeletonFields).toBe(renderedFields);
  });

  it("form (employee creation): as many fields as the loaded form has", async () => {
    const gate = mockGated("hr_admin", {
      "/api/employees": [employeeResponse({ id: 1 })],
      "/api/departments": [departmentResponse({ id: 1 })],
    });

    const { container } = renderApp("/employees/new");
    await waitForShell();
    const skeletonFields = countIn(container, "[data-skeleton-field]");
    expect(skeletonKinds(container)).toEqual(["form"]);

    await act(async () => {
      gate.resolve();
    });
    await screen.findByLabelText("ФИО");
    const renderedFields = countIn(container, ".ant-form-item-label");

    expect(skeletonFields).toBeGreaterThan(0);
    expect(skeletonFields).toBe(renderedFields);
  });

  it("record card (employee): as many blocks as the loaded card has", async () => {
    const employee = employeeResponse({ id: 2, full_name: "Петров Пётр Петрович", department_id: 1 });
    const gate = mockGated("hr_admin", {
      "/api/employees/2": employee,
      "/api/employees": [employee],
      "/api/departments": [departmentResponse({ id: 1 })],
    });

    const { container } = renderApp("/employees/2");
    await waitForShell();
    const skeletonBlocks = countIn(container, "[data-skeleton-block]");
    expect(skeletonKinds(container)).toEqual(["card"]);

    await act(async () => {
      gate.resolve();
    });
    await screen.findByRole("heading", { name: "Петров Пётр Петрович" });
    const renderedBlocks = countIn(container, ".ant-descriptions");

    expect(skeletonBlocks).toBeGreaterThan(0);
    expect(skeletonBlocks).toBe(renderedBlocks);
  });
});

describe("the calendar skeleton (the number is known before loading: one block per month)", () => {
  it("draws as many blocks as the loaded page has month grids", async () => {
    const year = new Date().getFullYear();
    const days = daysInYear(year).map((date) => ({
      date,
      day_type: isWeekendDate(date) ? "weekend" : "working",
    }));
    const gate = deferred();
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "system_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname.startsWith("/api/admin/production-calendar/")) {
        return gate.promise.then(() => ({ status: 200, body: { year, days } }));
      }
      return null;
    });

    const { container } = renderApp("/admin/production-calendar");
    await waitForShell();
    const skeletonBlocks = countIn(container, "[data-skeleton-block]");
    expect(skeletonKinds(container)).toEqual(["card"]);

    await act(async () => {
      gate.resolve();
    });
    await screen.findByText("Январь");
    // Заголовок месяца - единственный `strong` Typography.Text в сетке года.
    const renderedMonths = countIn(container, ".ant-typography strong");

    expect(skeletonBlocks).toBeGreaterThan(0);
    expect(renderedMonths).toBe(skeletonBlocks);
  });
});

describe("the \"My leave\" skeleton: a block plus the history table", () => {
  it("for one leave type, matches the block and the full page of history the page draws", async () => {
    const history = Array.from({ length: 12 }, (_, index) =>
      balanceEntryResponse({ id: index + 1, entry_date: "2026-03-10", quantity: "1.00" }),
    );
    const gate = deferred();
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return {
          status: 200,
          body: meResponse({ role: "employee", employee_id: 7, must_change_password: false }),
        };
      }
      if (method === "GET" && pathname === "/api/employees/7/balances") {
        return gate.promise.then(() => ({
          status: 200,
          body: balancesResponse([leaveTypeBalanceResponse({ history })]),
        }));
      }
      return null;
    });

    const { container } = renderApp("/my-leave");
    await waitForShell();
    const skeletonBlocks = countIn(container, "[data-skeleton-block]");
    const skeletonRows = container.querySelectorAll("[data-skeleton-row]");
    const skeletonColumns = skeletonRows[0]?.querySelectorAll("[data-skeleton-cell]").length;
    expect(skeletonKinds(container)).toEqual(["card", "table"]);

    await act(async () => {
      gate.resolve();
    });
    await screen.findByLabelText("Ежегодный оплачиваемый отпуск");
    // Один вид: один блок, 12 записей истории, страница истории - 10 строк, 5 колонок.
    expect(skeletonBlocks).toBe(countIn(container, ".ant-card"));
    expect(skeletonRows.length).toBe(countIn(container, "tbody .ant-table-row"));
    expect(skeletonColumns).toBe(countIn(container, "thead th"));
    expect(skeletonRows.length).toBe(10);
  });
});
