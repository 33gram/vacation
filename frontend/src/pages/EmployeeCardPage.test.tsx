import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

  it("sends the FULL set of fields on PUT after editing only one of them", async () => {
    setAccessToken("token");
    let putBody: Record<string, unknown> | null = null;
    setFetchHandler((method, pathname, init) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
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
      if (method === "PUT" && pathname === "/api/employees/2") {
        putBody = JSON.parse(init.body as string) as Record<string, unknown>;
        return { status: 200, body: { ...EMPLOYEE, position: putBody.position } };
      }
      return null;
    });

    renderApp("/employees/2");
    const user = userEvent.setup();

    await screen.findByRole("heading", { name: "Петров Пётр Петрович" });
    await user.click(screen.getByRole("button", { name: "Редактировать" }));

    const positionInput = await screen.findByLabelText("Должность");
    await user.clear(positionInput);
    await user.type(positionInput, "Ведущий инженер");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    // Успешный PUT закрывает форму правки обратно на просмотр.
    await screen.findByRole("button", { name: "Редактировать" });

    expect(putBody).not.toBeNull();
    // ПОЛНЫЙ состав полей — не только изменённое `position`.
    expect(putBody).toMatchObject({
      full_name: EMPLOYEE.full_name,
      personnel_number: EMPLOYEE.personnel_number,
      department_id: EMPLOYEE.department_id,
      position: "Ведущий инженер",
      hire_date: EMPLOYEE.hire_date,
      manager_id: EMPLOYEE.manager_id,
      email: EMPLOYEE.email,
      work_schedule_type: EMPLOYEE.work_schedule_type,
      leave_category: EMPLOYEE.leave_category,
      auth_type: EMPLOYEE.auth_type,
    });
  });

  it("deactivates only after confirming in the modal, as its own action, not part of PUT", async () => {
    setAccessToken("token");
    let deactivateCalled = false;
    let putCalled = false;
    setFetchHandler((method, pathname, init) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
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
      if (method === "PUT" && pathname === "/api/employees/2") {
        putCalled = true;
        return { status: 200, body: EMPLOYEE };
      }
      if (method === "POST" && pathname === "/api/employees/2/deactivate") {
        deactivateCalled = true;
        const body = JSON.parse(init.body as string) as { termination_date: string };
        return {
          status: 200,
          body: { ...EMPLOYEE, employment_status: "terminated", termination_date: body.termination_date },
        };
      }
      return null;
    });

    renderApp("/employees/2");
    const user = userEvent.setup();

    await screen.findByRole("heading", { name: "Петров Пётр Петрович" });
    await user.click(screen.getByRole("button", { name: "Деактивировать" }));

    // Открытие диалога само по себе не должно ничего отправлять —
    // подтверждение обязательно (FR-EMP-004).
    await screen.findByText(/будет скрыт/);
    expect(deactivateCalled).toBe(false);
    expect(putCalled).toBe(false);

    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Деактивировать" }));

    await screen.findByText("Уволен", { exact: false });
    expect(deactivateCalled).toBe(true);
    expect(putCalled).toBe(false);
  });
});
