import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { setAccessToken } from "../app/auth";
import { setFetchHandler } from "../test-utils/fetchMock";
import { departmentResponse, employeeResponse, meResponse } from "../test-utils/fixtures";
import { renderApp } from "../test-utils/renderApp";

const DEPARTMENTS = [
  departmentResponse({ id: 1, name: "Отдел разработки" }),
  departmentResponse({ id: 2, name: "Отдел кадров" }),
];

const IVANOV = employeeResponse({
  id: 1,
  full_name: "Иванов Иван Иванович",
  personnel_number: "1001",
  department_id: 1,
  manager_id: null,
});
const PETROV = employeeResponse({
  id: 2,
  full_name: "Петров Пётр Петрович",
  personnel_number: "1002",
  department_id: 2,
  manager_id: 1,
});
const SIDOROV_TERMINATED = employeeResponse({
  id: 3,
  full_name: "Сидоров Сидор Сидорович",
  personnel_number: "1003",
  department_id: 1,
  manager_id: 1,
  employment_status: "terminated",
  termination_date: "2026-01-10",
});

function mockBackend(employeesByIncludeTerminated: { shown: typeof IVANOV[]; hidden: typeof IVANOV[] }) {
  setAccessToken("token");
  setFetchHandler((method, pathname, _init, search) => {
    if (method === "GET" && pathname === "/api/auth/me") {
      return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
    }
    if (method === "GET" && pathname === "/api/departments") {
      return { status: 200, body: DEPARTMENTS };
    }
    if (method === "GET" && pathname === "/api/employees") {
      const includeTerminated = search.get("include_terminated") === "true";
      return {
        status: 200,
        body: includeTerminated
          ? [...employeesByIncludeTerminated.shown, ...employeesByIncludeTerminated.hidden]
          : employeesByIncludeTerminated.shown,
      };
    }
    return null;
  });
}

describe("employees list (T-M1-19, FR-EMP-001)", () => {
  it("hides terminated employees until the filter is switched on", async () => {
    mockBackend({ shown: [IVANOV, PETROV], hidden: [SIDOROV_TERMINATED] });

    renderApp("/employees");
    const user = userEvent.setup();

    // Ссылка на ФИО (первая колонка) — а не любое вхождение текста: "Иванов
    // Иван Иванович" отдельно встречается ещё и как ФИО руководителя в
    // строке Петрова (`manager_id: 1`).
    await screen.findByRole("link", { name: "Иванов Иван Иванович" });
    expect(screen.getByRole("link", { name: "Петров Пётр Петрович" })).toBeInTheDocument();
    // Отрицательный контроль: уволенный по умолчанию не показан.
    expect(screen.queryByText("Сидоров Сидор Сидорович")).not.toBeInTheDocument();

    await user.click(screen.getByRole("switch", { name: "Показывать уволенных" }));

    expect(await screen.findByRole("link", { name: "Сидоров Сидор Сидорович" })).toBeInTheDocument();
  });

  it("applies the name/personnel-number filter immediately and resets it via a link", async () => {
    mockBackend({ shown: [IVANOV, PETROV], hidden: [] });

    renderApp("/employees");
    const user = userEvent.setup();

    // Ссылка на ФИО (первая колонка), не любое вхождение текста: Иванов —
    // ещё и руководитель Петрова, его ФИО останется в строке Петрова даже
    // после того, как строка самого Иванова уйдёт по фильтру.
    await screen.findByRole("link", { name: "Иванов Иван Иванович" });
    expect(screen.getByRole("link", { name: "Петров Пётр Петрович" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("ФИО или табельный номер"), "Петров");

    // Применяется сразу, без отдельной кнопки поиска.
    expect(screen.queryByRole("link", { name: "Иванов Иван Иванович" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Петров Пётр Петрович" })).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "Сбросить" }));

    expect(await screen.findByRole("link", { name: "Иванов Иван Иванович" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Петров Пётр Петрович" })).toBeInTheDocument();
  });

  it("shows the department NAME and manager's full name, not raw ids", async () => {
    mockBackend({ shown: [IVANOV, PETROV], hidden: [] });

    renderApp("/employees");

    const petrovRow = (await screen.findByText("Петров Пётр Петрович")).closest("tr");
    if (!petrovRow) {
      throw new Error("Petrov's table row was not found");
    }
    const row = within(petrovRow);

    // Подразделение — по названию, не по department_id (2).
    expect(row.getByText("Отдел кадров")).toBeInTheDocument();
    // Руководитель — по ФИО, не по manager_id (1).
    expect(row.getByText("Иванов Иван Иванович")).toBeInTheDocument();

    // Отрицательный контроль: голый id нигде в строке не показан отдельной
    // ячейкой (только как часть табельного номера "1002", не сам по себе).
    expect(row.queryByText("2", { selector: "td" })).not.toBeInTheDocument();
    expect(row.queryByText("1", { selector: "td" })).not.toBeInTheDocument();
  });
});
