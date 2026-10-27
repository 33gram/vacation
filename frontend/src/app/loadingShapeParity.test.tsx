import { act, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setFetchHandler } from "../test-utils/fetchMock";
import {
  departmentResponse,
  employeeResponse,
  ldapSettingsResponse,
  meResponse,
} from "../test-utils/fixtures";
import { renderApp } from "../test-utils/renderApp";
import { setAccessToken } from "./auth";

// `ui.md` §4: каркас «той же высоты, что и реальные данные». Высота в пикселях в jsdom не
// проверяется (нет раскладки); проверяется то, что можно: число строк/колонок/полей/блоков
// каркаса РАВНО числу, которое та же страница рисует после загрузки. Каркас и итог считаются в
// одном тесте рядом - это проверка смысла правила, а не формы вызова.

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

/** Дожидается оболочки (значит, `SessionGate` позади и каркас - страницы), возвращает ключи
 * каркаса. */
async function skeletonOfPage(container: HTMLElement) {
  await screen.findByRole("button", { name: "Выйти" });
  return container;
}

const countIn = (root: ParentNode, selector: string) => root.querySelectorAll(selector).length;

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
    await skeletonOfPage(container);
    const skeletonRows = container.querySelectorAll("[data-skeleton-row]");
    const skeletonColumns = skeletonRows[0]?.querySelectorAll("[data-skeleton-cell]").length;

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
    await skeletonOfPage(container);
    const skeletonFields = countIn(container, "[data-skeleton-field]");

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
    await skeletonOfPage(container);
    const skeletonFields = countIn(container, "[data-skeleton-field]");

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
    await skeletonOfPage(container);
    const skeletonBlocks = countIn(container, "[data-skeleton-block]");

    await act(async () => {
      gate.resolve();
    });
    await screen.findByRole("heading", { name: "Петров Пётр Петрович" });
    const renderedBlocks = countIn(container, ".ant-descriptions");

    expect(skeletonBlocks).toBeGreaterThan(0);
    expect(skeletonBlocks).toBe(renderedBlocks);
  });
});
