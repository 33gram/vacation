import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { setAccessToken } from "../app/auth";
import { setFetchHandler } from "../test-utils/fetchMock";
import { departmentResponse, employeeResponse, meResponse } from "../test-utils/fixtures";
import { renderApp } from "../test-utils/renderApp";

function mockBackend(departments: ReturnType<typeof departmentResponse>[], employees: ReturnType<typeof employeeResponse>[]) {
  setAccessToken("token");
  setFetchHandler((method, pathname) => {
    if (method === "GET" && pathname === "/api/auth/me") {
      return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
    }
    if (method === "GET" && pathname === "/api/departments") {
      return { status: 200, body: departments };
    }
    if (method === "GET" && pathname === "/api/employees") {
      return { status: 200, body: employees };
    }
    return null;
  });
}

describe("departments tree, read only (T-M1-20, FR-EMP-002)", () => {
  it("shows the empty state when there are no departments", async () => {
    mockBackend([], []);

    renderApp("/departments");

    expect(await screen.findByText("Подразделений нет")).toBeInTheDocument();
  });

  it("shows an error state with a retry button when loading departments fails", async () => {
    setAccessToken("token");
    let departmentsCallCount = 0;
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/employees") {
        return { status: 200, body: [] };
      }
      if (method === "GET" && pathname === "/api/departments") {
        departmentsCallCount += 1;
        if (departmentsCallCount === 1) {
          return { status: 500, body: { detail: "internal error" } };
        }
        return { status: 200, body: [] };
      }
      return null;
    });

    renderApp("/departments");

    expect(await screen.findByText("Не удалось загрузить данные")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Повторить" }));

    expect(await screen.findByText("Подразделений нет")).toBeInTheDocument();
  });

  it("nests several levels and shows the department name and the head's full name in each node", async () => {
    const root = departmentResponse({ id: 1, name: "Компания", parent_id: null, head_employee_id: 1 });
    const division = departmentResponse({ id: 2, name: "Дивизион продаж", parent_id: 1, head_employee_id: null });
    const team = departmentResponse({ id: 3, name: "Команда продаж", parent_id: 2, head_employee_id: 2 });

    const ivanov = employeeResponse({ id: 1, full_name: "Иванов Иван Иванович" });
    const petrov = employeeResponse({ id: 2, full_name: "Петров Пётр Петрович" });

    mockBackend([root, division, team], [ivanov, petrov]);

    renderApp("/departments");

    expect(await screen.findByText("Компания — Иванов Иван Иванович")).toBeInTheDocument();
    expect(screen.getByText("Дивизион продаж — —")).toBeInTheDocument();
    expect(screen.getByText("Команда продаж — Петров Пётр Петрович")).toBeInTheDocument();
  });

  it("resolves a TERMINATED head's full name, not '—' as if there were none", async () => {
    const root = departmentResponse({ id: 1, name: "Отдел", parent_id: null, head_employee_id: 1 });
    const terminatedHead = employeeResponse({
      id: 1,
      full_name: "Сидоров Сидор Сидорович",
      employment_status: "terminated",
      termination_date: "2026-01-10",
    });

    mockBackend([root], [terminatedHead]);

    renderApp("/departments");

    expect(await screen.findByText("Отдел — Сидоров Сидор Сидорович")).toBeInTheDocument();
  });

  it("does not show raw department or employee ids on screen", async () => {
    const root = departmentResponse({ id: 42, name: "Отдел", parent_id: null, head_employee_id: 7 });
    const head = employeeResponse({ id: 7, full_name: "Кузнецова Мария Сергеевна" });

    mockBackend([root], [head]);

    renderApp("/departments");

    await screen.findByText("Отдел — Кузнецова Мария Сергеевна");
    // Отрицательный контроль: ни id подразделения, ни id руководителя не
    // показаны сами по себе.
    expect(screen.queryByText("42")).not.toBeInTheDocument();
    expect(screen.queryByText("7")).not.toBeInTheDocument();
  });
});
