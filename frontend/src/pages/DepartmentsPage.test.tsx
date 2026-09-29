import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

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

describe("departments create and rename (T-M1-20, FR-EMP-002)", () => {
  it("creates a root department (parent_id: null) via POST and shows it after refetch", async () => {
    let departments = [departmentResponse({ id: 1, name: "Существующий отдел", parent_id: null })];
    let postBody: Record<string, unknown> | null = null;
    setAccessToken("token");
    setFetchHandler((method, pathname, init) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/employees") {
        return { status: 200, body: [] };
      }
      if (method === "GET" && pathname === "/api/departments") {
        return { status: 200, body: departments };
      }
      if (method === "POST" && pathname === "/api/departments") {
        postBody = JSON.parse(init.body as string) as Record<string, unknown>;
        const created = departmentResponse({
          id: 2,
          name: postBody.name as string,
          parent_id: (postBody.parent_id as number | null) ?? null,
          head_employee_id: null,
        });
        departments = [...departments, created];
        return { status: 201, body: created };
      }
      return null;
    });

    renderApp("/departments");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Добавить подразделение" }));
    await user.type(screen.getByLabelText("Название"), "Новый отдел");
    await user.click(screen.getByRole("button", { name: "Создать" }));

    await vi.waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toEqual({ name: "Новый отдел", parent_id: null });
    expect(await screen.findByText(/Новый отдел/)).toBeInTheDocument();
  });

  it("creates a child department (parent_id: the clicked node's id) via POST", async () => {
    let departments = [departmentResponse({ id: 1, name: "Родитель", parent_id: null })];
    let postBody: Record<string, unknown> | null = null;
    setAccessToken("token");
    setFetchHandler((method, pathname, init) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/employees") {
        return { status: 200, body: [] };
      }
      if (method === "GET" && pathname === "/api/departments") {
        return { status: 200, body: departments };
      }
      if (method === "POST" && pathname === "/api/departments") {
        postBody = JSON.parse(init.body as string) as Record<string, unknown>;
        const created = departmentResponse({
          id: 2,
          name: postBody.name as string,
          parent_id: (postBody.parent_id as number | null) ?? null,
          head_employee_id: null,
        });
        departments = [...departments, created];
        return { status: 201, body: created };
      }
      return null;
    });

    renderApp("/departments");
    const user = userEvent.setup();

    await screen.findByText(/Родитель/);
    await user.click(screen.getByRole("button", { name: "Добавить дочернее" }));
    await user.type(screen.getByLabelText("Название"), "Дочерний отдел");
    await user.click(screen.getByRole("button", { name: "Создать" }));

    await vi.waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toEqual({ name: "Дочерний отдел", parent_id: 1 });
  });

  it("renaming sends the full three-field body — parent_id and head_employee_id unchanged", async () => {
    const department = departmentResponse({
      id: 1,
      name: "Старое название",
      parent_id: 5,
      head_employee_id: 9,
    });
    let putBody: Record<string, unknown> | null = null;
    setAccessToken("token");
    setFetchHandler((method, pathname, init) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/employees") {
        return { status: 200, body: [] };
      }
      if (method === "GET" && pathname === "/api/departments") {
        // Родитель (5) не входит в список специально — узел всё равно
        // должен стать корнем (сирота, см. buildDepartmentTree) и
        // остаться кликабельным, что важно только для рендера, не для
        // самого запроса; сам PUT ниже проверяется по телу, не по DOM.
        return { status: 200, body: [department] };
      }
      if (method === "PUT" && pathname === "/api/departments/1") {
        putBody = JSON.parse(init.body as string) as Record<string, unknown>;
        return { status: 200, body: { ...department, name: putBody.name } };
      }
      return null;
    });

    renderApp("/departments");
    const user = userEvent.setup();

    await screen.findByText(/Старое название/);
    await user.click(screen.getByRole("button", { name: "Переименовать" }));
    const nameInput = screen.getByLabelText("Название");
    await user.clear(nameInput);
    await user.type(nameInput, "Новое название");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    await vi.waitFor(() => expect(putBody).not.toBeNull());
    expect(putBody).toEqual({ name: "Новое название", parent_id: 5, head_employee_id: 9 });
  });

  it("invalidates the departments cache key after a mutation, so the employee form sees the new department too", async () => {
    let departments = [departmentResponse({ id: 1, name: "Существующий отдел", parent_id: null })];
    setAccessToken("token");
    let departmentsGetCount = 0;
    setFetchHandler((method, pathname, init) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/employees") {
        return { status: 200, body: [] };
      }
      if (method === "GET" && pathname === "/api/departments") {
        departmentsGetCount += 1;
        return { status: 200, body: departments };
      }
      if (method === "POST" && pathname === "/api/departments") {
        const body = JSON.parse(init.body as string) as Record<string, unknown>;
        const created = departmentResponse({ id: 2, name: body.name as string, parent_id: null });
        departments = [...departments, created];
        return { status: 201, body: created };
      }
      return null;
    });

    renderApp("/departments");
    const user = userEvent.setup();

    await screen.findByText(/Существующий отдел/);
    const countBeforeMutation = departmentsGetCount;

    await user.click(screen.getByRole("button", { name: "Добавить подразделение" }));
    await user.type(screen.getByLabelText("Название"), "Ещё один отдел");
    await user.click(screen.getByRole("button", { name: "Создать" }));

    await screen.findByText(/Ещё один отдел/);
    // Инвалидация ключа ["departments"], а не локальное обновление —
    // GET перезапрашивается заново после мутации.
    expect(departmentsGetCount).toBeGreaterThan(countBeforeMutation);
  });
});

describe("departments assign the head (T-M1-20, FR-EMP-002)", () => {
  it("offers only ACTIVE employees in the head dropdown, not a terminated one", async () => {
    const department = departmentResponse({ id: 1, name: "Отдел", parent_id: null });
    const activeEmployee = employeeResponse({ id: 1, full_name: "Иванов Иван Иванович" });
    const terminatedEmployee = employeeResponse({
      id: 2,
      full_name: "Петров Пётр Петрович",
      employment_status: "terminated",
      termination_date: "2026-01-10",
    });
    mockBackend([department], [activeEmployee, terminatedEmployee]);

    renderApp("/departments");
    const user = userEvent.setup();

    await screen.findByText(/Отдел/);
    await user.click(screen.getByRole("button", { name: "Назначить руководителя" }));
    await user.click(screen.getByLabelText("Руководитель"));

    expect(await screen.findByTitle("Иванов Иван Иванович")).toBeInTheDocument();
    // Отрицательный контроль: уволенный сервером будет отклонён, в
    // выпадающем списке его не предлагаем вовсе.
    expect(screen.queryByTitle("Петров Пётр Петрович")).not.toBeInTheDocument();
  });

  it("places a field-prefixed 'terminated head' server error under the head field, without the prefix shown", async () => {
    const department = departmentResponse({ id: 1, name: "Отдел", parent_id: 5, head_employee_id: null });
    const activeEmployee = employeeResponse({ id: 1, full_name: "Иванов Иван Иванович" });
    let putBody: Record<string, unknown> | null = null;
    setAccessToken("token");
    setFetchHandler((method, pathname, init) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/employees") {
        return { status: 200, body: [activeEmployee] };
      }
      if (method === "GET" && pathname === "/api/departments") {
        return { status: 200, body: [department] };
      }
      if (method === "PUT" && pathname === "/api/departments/1") {
        putBody = JSON.parse(init.body as string) as Record<string, unknown>;
        return {
          status: 400,
          body: { detail: "head_employee_id: уволенный сотрудник не может быть руководителем подразделения" },
        };
      }
      return null;
    });

    renderApp("/departments");
    const user = userEvent.setup();

    await screen.findByText(/Отдел/);
    await user.click(screen.getByRole("button", { name: "Назначить руководителя" }));
    await user.click(screen.getByLabelText("Руководитель"));
    await user.click(await screen.findByTitle("Иванов Иван Иванович"));
    await user.click(screen.getByRole("button", { name: "Назначить" }));

    const errorText = await screen.findByText("уволенный сотрудник не может быть руководителем подразделения");
    const headField = screen.getByLabelText("Руководитель").closest(".ant-form-item");
    expect(headField).toContainElement(errorText);
    // Отрицательный контроль: служебный префикс пользователю не показан.
    expect(screen.queryByText(/^head_employee_id:/)).not.toBeInTheDocument();

    await vi.waitFor(() => expect(putBody).not.toBeNull());
    // parent_id — прежний (5), не изменённый этим действием.
    expect(putBody).toEqual({ name: "Отдел", parent_id: 5, head_employee_id: 1 });
  });
});
