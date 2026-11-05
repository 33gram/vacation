import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { setAccessToken } from "../app/auth";
import { setFetchHandler } from "../test-utils/fetchMock";
import { meResponse } from "../test-utils/fixtures";
import { renderApp } from "../test-utils/renderApp";

function mockBackend(importResponse: { status: number; body?: unknown }) {
  setAccessToken("token");
  setFetchHandler((method, pathname) => {
    if (method === "GET" && pathname === "/api/auth/me") {
      return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
    }
    if (method === "GET" && pathname === "/api/employees") {
      return { status: 200, body: [] };
    }
    if (method === "POST" && pathname === "/api/employees/import") {
      return importResponse;
    }
    return null;
  });
}

function selectAFile(): File {
  return new File(["full_name,personnel_number\nA,1"], "employees.csv", { type: "text/csv" });
}

describe("employee import wizard (T-M1-19, FR-EMP-003)", () => {
  it("shows row-numbered errors in the report, not raw JSON", async () => {
    mockBackend({
      status: 200,
      body: {
        accepted_count: 2,
        rejected_count: 1,
        errors: [{ row_number: 5, field: "email", reason: "некорректный формат e-mail" }],
      },
    });

    const { container } = renderApp("/employees/import");
    const user = userEvent.setup();

    await screen.findByText("Выбрать файл");
    const fileInput = container.querySelector<HTMLInputElement>('input[type="file"]');
    if (!fileInput) {
      throw new Error("file input not found");
    }
    await user.upload(fileInput, selectAFile());
    await user.click(screen.getByRole("button", { name: "Загрузить" }));

    expect(await screen.findByText("Принято строк: 2")).toBeInTheDocument();
    expect(screen.getByText("Отклонено строк: 1")).toBeInTheDocument();
    // Построчная ошибка — номер строки и причина видны как читаемая
    // таблица, а не как сырой JSON-текст.
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText("email")).toBeInTheDocument();
    expect(screen.getByText("некорректный формат e-mail")).toBeInTheDocument();
    expect(screen.queryByText(/^\{.*"row_number".*\}$/)).not.toBeInTheDocument();
  });

  it("going back to step 1 keeps the previously selected file", async () => {
    mockBackend({
      status: 200,
      body: { accepted_count: 1, rejected_count: 0, errors: [] },
    });

    const { container } = renderApp("/employees/import");
    const user = userEvent.setup();

    await screen.findByText("Выбрать файл");
    const fileInput = container.querySelector<HTMLInputElement>('input[type="file"]');
    if (!fileInput) {
      throw new Error("file input not found");
    }
    await user.upload(fileInput, selectAFile());
    await screen.findByText("employees.csv");
    await user.click(screen.getByRole("button", { name: "Загрузить" }));

    await screen.findByText("Принято строк: 1");
    await user.click(screen.getByRole("button", { name: "Назад" }));

    expect(await screen.findByText("employees.csv")).toBeInTheDocument();
  });
});

describe("422 validation errors (T-M2-28)", () => {
  it("the upload has no form fields: the validation error is one general text, not the code fallback", async () => {
    mockBackend({
      status: 422,
      body: { detail: [{ loc: ["body", "file"], msg: "Обязательное поле.", type: "missing" }] },
    });

    const { container } = renderApp("/employees/import");
    const user = userEvent.setup();
    await screen.findByText("Выбрать файл");
    const fileInput = container.querySelector<HTMLInputElement>('input[type="file"]');
    if (!fileInput) {
      throw new Error("file input not found");
    }
    await user.upload(fileInput, selectAFile());
    await user.click(screen.getByRole("button", { name: "Загрузить" }));

    expect(await screen.findByText("file: Обязательное поле.")).toBeInTheDocument();
    expect(screen.queryByText(/Не удалось загрузить файл/)).not.toBeInTheDocument();
  });
});
