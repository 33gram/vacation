import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { setAccessToken } from "../../app/auth";
import { setFetchHandler } from "../../test-utils/fetchMock";
import { meResponse } from "../../test-utils/fixtures";
import { renderApp } from "../../test-utils/renderApp";

const CURRENT_YEAR = new Date().getFullYear();

function mockBackend(
  daysByYear: Record<number, { date: string; day_type: string }[]>,
  options: { role?: string; getStatus?: number } = {},
) {
  setAccessToken("token");
  setFetchHandler((method, pathname) => {
    if (method === "GET" && pathname === "/api/auth/me") {
      return {
        status: 200,
        body: meResponse({ role: options.role ?? "system_admin", must_change_password: false }),
      };
    }
    const match = pathname.match(/^\/api\/admin\/production-calendar\/(\d+)$/);
    if (method === "GET" && match) {
      if (options.getStatus && options.getStatus !== 200) {
        return { status: options.getStatus, body: { detail: "internal error" } };
      }
      const year = Number(match[1]);
      return { status: 200, body: { year, days: daysByYear[year] ?? [] } };
    }
    return null;
  });
}

describe("production calendar, read only (T-M1-22, FR-ADMIN-003)", () => {
  it("shows the loading state, then the grid once data arrives", async () => {
    mockBackend({ [CURRENT_YEAR]: [{ date: `${CURRENT_YEAR}-01-01`, day_type: "holiday" }] });

    renderApp("/admin/production-calendar");

    expect(
      await screen.findByRole("heading", { name: "Производственный календарь" }),
    ).toBeInTheDocument();
    expect(await screen.findByTitle(new RegExp(`01\\.01\\.${CURRENT_YEAR} — Праздник`))).toBeInTheDocument();
  });

  it("shows an error state with Retry when the request fails, and recovers after retry", async () => {
    let callCount = 0;
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "system_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === `/api/admin/production-calendar/${CURRENT_YEAR}`) {
        callCount += 1;
        if (callCount === 1) {
          return { status: 500, body: { detail: "internal error" } };
        }
        return { status: 200, body: { year: CURRENT_YEAR, days: [] } };
      }
      return null;
    });

    renderApp("/admin/production-calendar");

    expect(await screen.findByText("Не удалось загрузить данные")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Повторить" }));

    expect(await screen.findByText(`Календарь на ${CURRENT_YEAR} год не заполнен`)).toBeInTheDocument();
  });

  it("shows the empty state for a year with no loaded days, not an empty grid or an error", async () => {
    mockBackend({ [CURRENT_YEAR]: [] });

    renderApp("/admin/production-calendar");

    expect(await screen.findByText(`Календарь на ${CURRENT_YEAR} год не заполнен`)).toBeInTheDocument();
    expect(screen.queryByText("Не удалось загрузить данные")).not.toBeInTheDocument();
  });

  it("renders a full grid for a loaded year, with day types distinguishable by more than color", async () => {
    mockBackend({
      [CURRENT_YEAR]: [
        { date: `${CURRENT_YEAR}-01-01`, day_type: "holiday" },
        { date: `${CURRENT_YEAR}-01-02`, day_type: "working" },
      ],
    });

    renderApp("/admin/production-calendar");

    // Отрицательный контроль к самой идее теста: если бы различие было
    // только цветом, ни один из этих запросов не смог бы найти ячейку —
    // здесь ищем по атрибутивной метке (`title`), не по стилю.
    expect(await screen.findByTitle(new RegExp(`01\\.01\\.${CURRENT_YEAR} — Праздник`))).toBeInTheDocument();
    expect(screen.getByTitle(new RegExp(`02\\.01\\.${CURRENT_YEAR} — Рабочий`))).toBeInTheDocument();
    // День без day_type в ответе — показан отдельной меткой, не спутан
    // ни с одним из трёх настоящих типов.
    expect(screen.getByTitle(new RegExp(`03\\.01\\.${CURRENT_YEAR} — Не задан`))).toBeInTheDocument();
  });

  it("switches the year and loads that year's data separately", async () => {
    mockBackend({
      [CURRENT_YEAR]: [{ date: `${CURRENT_YEAR}-01-01`, day_type: "working" }],
      [CURRENT_YEAR + 1]: [{ date: `${CURRENT_YEAR + 1}-01-01`, day_type: "holiday" }],
    });

    renderApp("/admin/production-calendar");
    await screen.findByTitle(new RegExp(`01\\.01\\.${CURRENT_YEAR} — Рабочий`));

    const user = userEvent.setup();
    await user.click(screen.getByRole("combobox", { name: "Год" }));
    await user.click(await screen.findByTitle(String(CURRENT_YEAR + 1)));

    expect(
      await screen.findByTitle(new RegExp(`01\\.01\\.${CURRENT_YEAR + 1} — Праздник`)),
    ).toBeInTheDocument();
    expect(
      screen.queryByTitle(new RegExp(`01\\.01\\.${CURRENT_YEAR} — Рабочий`)),
    ).not.toBeInTheDocument();
  });
});

describe("production calendar is restricted to system_admin (T-M1-22 review)", () => {
  it("does not show the menu item to hr_admin", async () => {
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
      }
      return null;
    });

    renderApp("/");

    await screen.findByText("Сотрудники");
    expect(screen.queryByText("Производственный календарь")).not.toBeInTheDocument();
  });

  it("denies hr_admin direct navigation to the calendar with 'Доступ запрещён', the same mechanism as other routes", async () => {
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
      }
      return null;
    });

    renderApp("/admin/production-calendar");

    expect(
      await screen.findByText("Доступ запрещён. Обратитесь к HR-администратору."),
    ).toBeInTheDocument();
    // Отрицательный контроль: экран календаря не пытается ничего
    // запросить у сервера для запрещённой роли — до `GET` дело не
    // доходит (мок вернул бы `null` и тест упал бы явной ошибкой из
    // `fetchMock.ts`, если бы дошло).
  });
});
