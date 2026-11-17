import { focusManager } from "@tanstack/react-query";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

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

  it("shows the shift-schedule flag in the card for a shift employee (FR-EMP-001/7)", async () => {
    // Только "отображается в карточке" - часть критерия про отгулы
    // (FR-DAYOFF-001) и общий календарь (FR-CAL-001) не проверяется
    // здесь: ни один из этих разделов ещё не реализован (M3/M5), и
    // учитывать сменный график им пока нечем.
    const shiftEmployee = employeeResponse({
      id: 3,
      full_name: "Сидоров Сидор Сидорович",
      department_id: 1,
      manager_id: 1,
      work_schedule_type: "shift",
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
        return { status: 200, body: [MANAGER, shiftEmployee] };
      }
      if (method === "GET" && pathname === "/api/employees/3") {
        return { status: 200, body: shiftEmployee };
      }
      return null;
    });

    renderApp("/employees/3");

    await screen.findByRole("heading", { name: "Сидоров Сидор Сидорович" });
    expect(screen.getByText("Сменный")).toBeInTheDocument();
    // Отрицательный контроль на саму идею теста: стандартный график не
    // показан рядом - подпись зависит от присланного значения, а не
    // захардкожена.
    expect(screen.queryByText("Стандартный")).not.toBeInTheDocument();
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

describe("422 validation errors (T-M2-28)", () => {
  it("deactivation: the date has no form field, the validation error is one general text in the dialog", async () => {
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
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
      if (method === "POST" && pathname === "/api/employees/2/deactivate") {
        return {
          status: 422,
          body: {
            detail: [
              { loc: ["body", "termination_date"], msg: "Неверная дата, нужен формат ГГГГ-ММ-ДД.", type: "date_parsing" },
            ],
          },
        };
      }
      return null;
    });

    renderApp("/employees/2");
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "Петров Пётр Петрович" });
    await user.click(screen.getByRole("button", { name: "Деактивировать" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Деактивировать" }));

    expect(
      await within(dialog).findByText("termination_date: Неверная дата, нужен формат ГГГГ-ММ-ДД."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Не удалось деактивировать сотрудника.")).not.toBeInTheDocument();
  });
});


describe("a termination date before the hire date (FR-EMP-004/3, T-M2-41)", () => {
  it("shows the server's refusal in the dialog without the machine-readable field prefix", async () => {
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
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
      if (method === "POST" && pathname === "/api/employees/2/deactivate") {
        return {
          status: 400,
          body: {
            detail: "termination_date: Дата увольнения не может быть раньше даты приёма (10.03.2026).",
          },
        };
      }
      return null;
    });

    renderApp("/employees/2");
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "Петров Пётр Петрович" });
    await user.click(screen.getByRole("button", { name: "Деактивировать" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Деактивировать" }));

    expect(
      await within(dialog).findByText("Дата увольнения не может быть раньше даты приёма (10.03.2026)."),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText(/termination_date:/)).not.toBeInTheDocument();
    expect(screen.queryByText("Не удалось деактивировать сотрудника.")).not.toBeInTheDocument();
  });
});

describe("the default termination date is the server's business day (T-M2-38)", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllEnvs();
  });

  /** Бэкенд карточки; `me` - произвольное тело, чтобы можно было убрать `today`. Возвращает то,
   * что ушло в `POST /deactivate`. */
  function mockCard(me: Record<string, unknown>) {
    setAccessToken("token");
    const sent: string[] = [];
    setFetchHandler((method, pathname, init) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: me };
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
      if (method === "POST" && pathname === "/api/employees/2/deactivate") {
        const body = JSON.parse(init.body as string) as { termination_date: string };
        sent.push(body.termination_date);
        return {
          status: 200,
          body: { ...EMPLOYEE, employment_status: "terminated", termination_date: body.termination_date },
        };
      }
      return null;
    });
    return sent;
  }

  async function openDialog() {
    const user = userEvent.setup();
    renderApp("/employees/2");
    await screen.findByRole("heading", { name: "Петров Пётр Петрович" });
    await user.click(screen.getByRole("button", { name: "Деактивировать" }));
    const dialog = await screen.findByRole("dialog");
    return { user, dialog };
  }

  it.each([
    ["the browser clock runs six days BEHIND", "2026-10-20T12:00:00Z"],
    ["the browser clock runs six days AHEAD", "2026-11-01T12:00:00Z"],
  ])("%s: the preset date is the session day, and exactly it is sent", async (_name, nowUtc) => {
    vi.stubEnv("TZ", "UTC");
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(nowUtc));
    const sent = mockCard(meResponse({ role: "hr_admin", today: "2026-10-26" }));
    const { user, dialog } = await openDialog();

    expect(within(dialog).getByLabelText("Дата увольнения")).toHaveValue("26.10.2026");
    await user.click(within(dialog).getByRole("button", { name: "Деактивировать" }));

    await screen.findByText("Уволен", { exact: false });
    expect(sent).toEqual(["2026-10-26"]);
  });

  it("the window takes the day as a SNAPSHOT at opening: a later answer of me does not move the date under the hand (PR #197 review)", async () => {
    setAccessToken("token");
    let meCalls = 0;
    const sent: string[] = [];
    setFetchHandler((method, pathname, init) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        meCalls += 1;
        // Первый ответ - 31.12, после возврата фокуса - уже 01.01.
        const today = meCalls === 1 ? "2026-12-31" : "2027-01-01";
        return { status: 200, body: meResponse({ role: "hr_admin", today }) };
      }
      if (method === "GET" && pathname === "/api/departments") return { status: 200, body: DEPARTMENTS };
      if (method === "GET" && pathname === "/api/employees") return { status: 200, body: [MANAGER, EMPLOYEE] };
      if (method === "GET" && pathname === "/api/employees/2") return { status: 200, body: EMPLOYEE };
      if (method === "POST" && pathname === "/api/employees/2/deactivate") {
        const body = JSON.parse(init.body as string) as { termination_date: string };
        sent.push(body.termination_date);
        return { status: 200, body: { ...EMPLOYEE, employment_status: "terminated", termination_date: body.termination_date } };
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp("/employees/2");
    await screen.findByRole("heading", { name: "Петров Пётр Петрович" });
    await user.click(screen.getByRole("button", { name: "Деактивировать" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Дата увольнения")).toHaveValue("31.12.2026");

    // Возврат фокуса на вкладку при открытом окне: `me` перезапрошен и отвечает новым днём.
    act(() => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });
    await waitFor(() => expect(meCalls).toBeGreaterThanOrEqual(2));
    await new Promise((resolve) => setTimeout(resolve, 50)); // ответ дошёл до сессии

    expect(within(dialog).getByLabelText("Дата увольнения")).toHaveValue("31.12.2026");
    await user.click(within(dialog).getByRole("button", { name: "Деактивировать" }));
    await screen.findByText("Уволен", { exact: false });
    expect(sent).toEqual(["2026-12-31"]);
  });

  it("the window opened AGAIN takes a new snapshot: the new day, and an earlier pick is dropped (PR #197 review)", async () => {
    setAccessToken("token");
    let meCalls = 0;
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        meCalls += 1;
        const today = meCalls === 1 ? "2026-12-31" : "2027-01-01";
        return { status: 200, body: meResponse({ role: "hr_admin", today }) };
      }
      if (method === "GET" && pathname === "/api/departments") return { status: 200, body: DEPARTMENTS };
      if (method === "GET" && pathname === "/api/employees") return { status: 200, body: [MANAGER, EMPLOYEE] };
      if (method === "GET" && pathname === "/api/employees/2") return { status: 200, body: EMPLOYEE };
      return null;
    });
    const user = userEvent.setup();
    renderApp("/employees/2");
    await screen.findByRole("heading", { name: "Петров Пётр Петрович" });
    await user.click(screen.getByRole("button", { name: "Деактивировать" }));
    let dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText("Дата увольнения");
    fireEvent.change(input, { target: { value: "15.03.2027" } });
    fireEvent.keyDown(input, { key: "Enter", keyCode: 13 });
    await waitFor(() => expect(input).toHaveValue("15.03.2027")); // выбор пользователя перекрывает умолчание

    await user.click(within(dialog).getByRole("button", { name: "Отмена" }));
    act(() => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });
    await waitFor(() => expect(meCalls).toBeGreaterThanOrEqual(2));
    await new Promise((resolve) => setTimeout(resolve, 50));
    // Закрытое окно остаётся в DOM скрытым: кнопка страницы - первая из одноимённых.
    await user.click(screen.getAllByRole("button", { name: "Деактивировать" })[0]);
    dialog = await screen.findByRole("dialog");

    expect(within(dialog).getByLabelText("Дата увольнения")).toHaveValue("01.01.2027");
  });

  it("a session day that is not a calendar date leaves the date empty (PR #197 review, Д-3)", async () => {
    const { today: _omitted, ...rest } = meResponse({ role: "hr_admin" });
    mockCard({ ...rest, today: "2026-02-30" });
    const { dialog } = await openDialog();

    expect(within(dialog).getByLabelText("Дата увольнения")).toHaveValue("");
    expect(within(dialog).getByRole("button", { name: "Деактивировать" })).toBeDisabled();
  });

  it("a session answer WITHOUT today: the date is empty, the button is off until a date is chosen (Ф-1)", async () => {
    vi.stubEnv("TZ", "UTC");
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-06-15T12:00:00Z"));
    const { today: _omitted, ...withoutToday } = meResponse({ role: "hr_admin" });
    const sent = mockCard(withoutToday);
    const { user, dialog } = await openDialog();

    const input = within(dialog).getByLabelText("Дата увольнения");
    const confirm = within(dialog).getByRole("button", { name: "Деактивировать" });
    expect(input).toHaveValue(""); // часы браузера (15.06) не подставлены
    expect(confirm).toBeDisabled();

    fireEvent.change(input, { target: { value: "15.11.2026" } });
    fireEvent.keyDown(input, { key: "Enter", keyCode: 13 });
    await vi.waitFor(() => expect(confirm).toBeEnabled());
    await user.click(confirm);

    await screen.findByText("Уволен", { exact: false });
    expect(sent).toEqual(["2026-11-15"]);
  });
});
