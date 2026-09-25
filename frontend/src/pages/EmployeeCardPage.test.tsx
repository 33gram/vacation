import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setAccessToken } from "../app/auth";
import { setFetchHandler } from "../test-utils/fetchMock";
import { departmentResponse, employeeResponse, meResponse } from "../test-utils/fixtures";
import { renderApp } from "../test-utils/renderApp";

const DEPARTMENTS = [departmentResponse({ id: 1, name: "Отдел кадров" })];
const MANAGER = employeeResponse({ id: 1, full_name: "Иванов Иван Иванович", manager_id: null });
const EMPLOYEE = employeeResponse({
  id: 2,
  full_name: "Петров Пётр Петрович",
  department_id: 1,
  manager_id: 1,
});

function mockBackend(role: string) {
  setAccessToken("token");
  setFetchHandler((method, pathname) => {
    if (method === "GET" && pathname === "/api/auth/me") {
      return { status: 200, body: meResponse({ role, must_change_password: false }) };
    }
    if (method === "GET" && pathname === "/api/departments") {
      return { status: 200, body: DEPARTMENTS };
    }
    if (method === "GET" && pathname === "/api/employees") {
      return { status: 200, body: [MANAGER, EMPLOYEE] };
    }
    if (method === "GET" && pathname === "/api/employees/2") {
      return { status: 200, body: EMPLOYEE };
    }
    return null;
  });
}

describe("employee card (T-M1-19, FR-EMP-001)", () => {
  it("shows the department NAME and manager's full name, not raw ids", async () => {
    mockBackend("hr_admin");

    renderApp("/employees/2");

    await screen.findByRole("heading", { name: "Петров Пётр Петрович" });
    expect(screen.getByText("Отдел кадров")).toBeInTheDocument();
    expect(screen.getByText("Иванов Иван Иванович")).toBeInTheDocument();
    // Отрицательный контроль: голые id (department_id: 1, manager_id: 1) не
    // показаны как отдельный текстовый узел.
    expect(screen.queryByText("1", { exact: true })).not.toBeInTheDocument();
  });

  it.each(["employee", "accountant"])("denies %s the card, showing 'Нет доступа к записи'", async (role) => {
    mockBackend(role);

    renderApp("/employees/2");

    expect(
      await screen.findByText("Доступ запрещён. Обратитесь к HR-администратору."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Петров Пётр Петрович")).not.toBeInTheDocument();
  });

  it("lets hr_admin in, unlike employee/accountant above", async () => {
    mockBackend("hr_admin");

    renderApp("/employees/2");

    expect(await screen.findByRole("heading", { name: "Петров Пётр Петрович" })).toBeInTheDocument();
    expect(
      screen.queryByText("Доступ запрещён. Обратитесь к HR-администратору."),
    ).not.toBeInTheDocument();
  });
});
