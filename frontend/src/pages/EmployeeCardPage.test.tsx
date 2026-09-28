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
    // `auth_type: "local"`, не фикстурный дефолт "ldap" — сверка ниже
    // сравнивает с литералом "local", а не с `LOCAL_EMPLOYEE.auth_type`:
    // тест обязан упасть, если код где-то подставит "ldap" вместо
    // прочитанного значения (T-M1-52 — именно так туда и проникает
    // регрессия, молча стирая пароль локального сотрудника), а сверка со
    // значением из той же фикстуры прошла бы даже при такой подмене.
    const LOCAL_EMPLOYEE = employeeResponse({
      id: 2,
      full_name: "Петров Пётр Петрович",
      department_id: 1,
      manager_id: 1,
      auth_type: "local",
    });
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
        return { status: 200, body: [MANAGER, LOCAL_EMPLOYEE] };
      }
      if (method === "GET" && pathname === "/api/employees/2") {
        return { status: 200, body: LOCAL_EMPLOYEE };
      }
      if (method === "PUT" && pathname === "/api/employees/2") {
        putBody = JSON.parse(init.body as string) as Record<string, unknown>;
        return { status: 200, body: { ...LOCAL_EMPLOYEE, position: putBody.position } };
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
    // ПОЛНЫЙ состав полей — не только изменённое `position`, и
    // `auth_type` буквально "local", а не то, что лежит в фикстуре.
    expect(putBody).toMatchObject({
      full_name: LOCAL_EMPLOYEE.full_name,
      personnel_number: LOCAL_EMPLOYEE.personnel_number,
      department_id: LOCAL_EMPLOYEE.department_id,
      position: "Ведущий инженер",
      hire_date: LOCAL_EMPLOYEE.hire_date,
      manager_id: LOCAL_EMPLOYEE.manager_id,
      email: LOCAL_EMPLOYEE.email,
      work_schedule_type: LOCAL_EMPLOYEE.work_schedule_type,
      leave_category: LOCAL_EMPLOYEE.leave_category,
      auth_type: "local",
    });
  });

  it("editing a local-auth employee without changing the login method hides the password field and sends new_password: null", async () => {
    const LOCAL_EMPLOYEE = employeeResponse({
      id: 2,
      full_name: "Петров Пётр Петрович",
      department_id: 1,
      manager_id: 1,
      auth_type: "local",
    });
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
        return { status: 200, body: [MANAGER, LOCAL_EMPLOYEE] };
      }
      if (method === "GET" && pathname === "/api/employees/2") {
        return { status: 200, body: LOCAL_EMPLOYEE };
      }
      if (method === "PUT" && pathname === "/api/employees/2") {
        putBody = JSON.parse(init.body as string) as Record<string, unknown>;
        return { status: 200, body: LOCAL_EMPLOYEE };
      }
      return null;
    });

    renderApp("/employees/2");
    const user = userEvent.setup();

    await screen.findByRole("heading", { name: "Петров Пётр Петрович" });
    await user.click(screen.getByRole("button", { name: "Редактировать" }));
    await screen.findByLabelText("Должность");

    // Способ входа уже локальный (радио-кнопка предзаполнена) — поле
    // пароля не должно появляться, пока способ входа не меняется
    // (T-M1-34: сервис отклоняет пароль без смены способа входа).
    expect(screen.queryByLabelText("Новый пароль")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findByRole("button", { name: "Редактировать" });

    expect(putBody).not.toBeNull();
    expect(putBody).toMatchObject({ auth_type: "local", new_password: null });
  });

  it("switching from LDAP to local auth shows the password field, and the typed password goes into new_password", async () => {
    const LDAP_EMPLOYEE = employeeResponse({
      id: 2,
      full_name: "Петров Пётр Петрович",
      department_id: 1,
      manager_id: 1,
      auth_type: "ldap",
    });
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
        return { status: 200, body: [MANAGER, LDAP_EMPLOYEE] };
      }
      if (method === "GET" && pathname === "/api/employees/2") {
        return { status: 200, body: LDAP_EMPLOYEE };
      }
      if (method === "GET" && pathname === "/api/auth/password-policy") {
        return { status: 200, body: [] };
      }
      if (method === "PUT" && pathname === "/api/employees/2") {
        putBody = JSON.parse(init.body as string) as Record<string, unknown>;
        return { status: 200, body: { ...LDAP_EMPLOYEE, auth_type: "local" } };
      }
      return null;
    });

    renderApp("/employees/2");
    const user = userEvent.setup();

    await screen.findByRole("heading", { name: "Петров Пётр Петрович" });
    await user.click(screen.getByRole("button", { name: "Редактировать" }));
    await screen.findByLabelText("Должность");

    expect(screen.queryByLabelText("Новый пароль")).not.toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "Локальный пароль" }));
    const passwordInput = await screen.findByLabelText("Новый пароль");
    await user.type(passwordInput, "NewPassword1!");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findByRole("button", { name: "Редактировать" });

    expect(putBody).not.toBeNull();
    expect(putBody).toMatchObject({ auth_type: "local", new_password: "NewPassword1!" });
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

  it.each([
    ["ldap", "Корпоративный каталог (LDAP)"],
    ["local", "Локальный пароль"],
  ])(
    "shows 'Способ входа' in view mode for auth_type %s, without opening the edit form",
    async (authType, expectedLabel) => {
      const employeeWithAuthType = employeeResponse({
        id: 2,
        full_name: "Петров Пётр Петрович",
        department_id: 1,
        manager_id: 1,
        auth_type: authType as "ldap" | "local",
      });
      setAccessToken("token");
      setFetchHandler((method, pathname) => {
        if (method === "GET" && pathname === "/api/auth/me") {
          return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
        }
        if (method === "GET" && pathname === "/api/departments") {
          return { status: 200, body: DEPARTMENTS };
        }
        if (method === "GET" && pathname === "/api/employees") {
          return { status: 200, body: [MANAGER, employeeWithAuthType] };
        }
        if (method === "GET" && pathname === "/api/employees/2") {
          return { status: 200, body: employeeWithAuthType };
        }
        return null;
      });

      renderApp("/employees/2");

      await screen.findByRole("heading", { name: "Петров Пётр Петрович" });
      // Без нажатия «Редактировать» — до T-M1-19 review способ входа было
      // видно только в форме правки.
      expect(screen.getByText(expectedLabel)).toBeInTheDocument();
    },
  );
});
