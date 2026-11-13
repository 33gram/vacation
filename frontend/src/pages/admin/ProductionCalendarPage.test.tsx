import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setAccessToken } from "../../app/auth";
import { daysInYear, isWeekendDate } from "../../entities/productionCalendar";
import { setFetchHandler } from "../../test-utils/fetchMock";
import { meResponse, SESSION_TODAY } from "../../test-utils/fixtures";
import { renderApp } from "../../test-utils/renderApp";

// Год - из бизнес-дня сессии (`T-M2-36`), как у страницы; часы браузера в тестах не участвуют
// (раньше здесь было `new Date().getFullYear()` - правка существующего теста по задаче).
const CURRENT_YEAR = Number(SESSION_TODAY.slice(0, 4));

/** Полностью заполненный год ("рабочий" везде, кроме суббот/воскресений)
 * — фикстура для тестов правки/сохранения, где сам факт «год не пуст и
 * не содержит незаданных дней» не является предметом теста (это
 * отдельно проверяют тесты коммита 2 и коммита 4). */
function fullYearFixture(year: number): { date: string; day_type: string }[] {
  return daysInYear(year).map((date) => ({
    date,
    day_type: isWeekendDate(date) ? "weekend" : "working",
  }));
}

function ddmmyyyy(isoDate: string): string {
  const [y, m, d] = isoDate.split("-");
  return `${d}\\.${m}\\.${y}`;
}

function mockBackend(
  daysByYear: Record<number, { date: string; day_type: string }[]>,
  options: { role?: string; getStatus?: number; me?: Record<string, unknown> } = {},
) {
  setAccessToken("token");
  setFetchHandler((method, pathname) => {
    if (method === "GET" && pathname === "/api/auth/me") {
      return {
        status: 200,
        body: {
          ...meResponse({ role: options.role ?? "system_admin", must_change_password: false }),
          ...options.me,
        },
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

describe("production calendar access: HR and system administrator (T-M2-23)", () => {
  /** `calendarReads` - число обращений к `GET` календаря: для запрещённой роли оно должно быть
   * НУЛЕВЫМ, а не просто «экран отказа показан» (мок отвечает 200 любой роли, поэтому только
   * счётчик отличает «защита сработала до запроса» от «запрос ушёл, потом показали отказ»). */
  function mockRole(role: string) {
    const calls = { calendarReads: 0 };
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role, must_change_password: false }) };
      }
      if (method === "GET" && pathname.startsWith("/api/admin/production-calendar/")) {
        calls.calendarReads += 1;
        return { status: 200, body: { year: CURRENT_YEAR, days: fullYearFixture(CURRENT_YEAR) } };
      }
      return null;
    });
    return calls;
  }

  it.each(["hr_admin", "system_admin"])("shows the menu item to %s and opens the screen", async (role) => {
    const calls = mockRole(role);

    renderApp("/admin/production-calendar");

    expect(await screen.findByRole("heading", { name: "Производственный календарь" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Производственный календарь" })).toBeInTheDocument();
    expect(calls.calendarReads).toBeGreaterThan(0);
  });

  it.each(["employee", "accountant"])("hides the menu item from %s", async (role) => {
    mockRole(role);

    renderApp("/");

    await screen.findByRole("button", { name: "Выйти" });
    expect(screen.queryByRole("menuitem", { name: "Производственный календарь" })).not.toBeInTheDocument();
  });

  it.each(["employee", "accountant"])(
    "denies %s direct navigation with 'Доступ запрещён' and sends NO request for the calendar",
    async (role) => {
      const calls = mockRole(role);

      renderApp("/admin/production-calendar");

      expect(
        await screen.findByText("Доступ запрещён. Обратитесь к HR-администратору."),
      ).toBeInTheDocument();
      expect(calls.calendarReads).toBe(0);
    },
  );
});

describe("production calendar editing and saving (T-M1-22, FR-ADMIN-003)", () => {
  // FR-ADMIN-003/1 (интерфейс редактирования производственного
  // календаря) - this screen, end to end, is the whole implementation
  // of that one criterion; this test is the clearest single
  // demonstration of "edit", the loading/grid tests above cover
  // "upload"/"view".
  it("editing one day sends the FULL year in the PUT body, not just the changed day", async () => {
    const year = CURRENT_YEAR;
    const fixture = fullYearFixture(year);
    type PutBody = { days: { date: string; day_type: string }[] };
    let putBody: PutBody | null = null;
    setAccessToken("token");
    setFetchHandler((method, pathname, init) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "system_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === `/api/admin/production-calendar/${year}`) {
        return { status: 200, body: { year, days: fixture } };
      }
      if (method === "PUT" && pathname === `/api/admin/production-calendar/${year}`) {
        const parsed = JSON.parse(init.body as string) as PutBody;
        putBody = parsed;
        return { status: 200, body: { year, days: parsed.days } };
      }
      return null;
    });

    renderApp("/admin/production-calendar");

    const firstDayTitle = new RegExp(`01\\.01\\.${year} — `);
    const user = userEvent.setup();
    await user.click(await screen.findByTitle(firstDayTitle));
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    await vi.waitFor(() => expect(putBody).not.toBeNull());
    const sentBody = putBody!;
    // Главный отрицательный контроль (проверено прогоном до реализации,
    // см. тело коммита): тело обязано содержать ВЕСЬ год, а не один
    // изменённый день.
    expect(sentBody.days).toHaveLength(daysInYear(year).length);
    expect(sentBody.days.map((d) => d.date)).toContain(`${year}-06-15`);
    // И сам клик подействовал: тип 1 января в отправленном теле отличается
    // от исходного значения фикстуры.
    const originalJan1 = fixture.find((d) => d.date === `${year}-01-01`)?.day_type;
    const sentJan1 = sentBody.days.find((d) => d.date === `${year}-01-01`)?.day_type;
    expect(sentJan1).not.toBe(originalJan1);
  });

  it("shows the server's own error text on a failed save, above the action, not rewritten", async () => {
    const year = CURRENT_YEAR;
    const fixture = fullYearFixture(year);
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "system_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === `/api/admin/production-calendar/${year}`) {
        return { status: 200, body: { year, days: fixture } };
      }
      if (method === "PUT" && pathname === `/api/admin/production-calendar/${year}`) {
        return { status: 400, body: { detail: `дата ${year}-01-01 не относится к году ${year + 1}` } };
      }
      return null;
    });

    renderApp("/admin/production-calendar");
    const user = userEvent.setup();
    await screen.findByTitle(new RegExp(`01\\.01\\.${year} — `));
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    const errorNode = await screen.findByText(
      `дата ${year}-01-01 не относится к году ${year + 1}`,
    );
    // `ui.md` §7, «Место ошибки»: ошибка всей операции — над кнопками
    // действия. Проверка через положение узлов в документе
    // (`compareDocumentPosition`), а не через индекс в верстке или CSS:
    // если ошибка идёт раньше кнопки, у пары (ошибка, кнопка) установлен
    // бит DOCUMENT_POSITION_FOLLOWING — кнопка СЛЕДУЕТ за ошибкой.
    // Имя ищем по подстроке, а не строгим равенством: у antd `Button` с
    // `loading` иконка "загрузки" перестаёт снимать себя из дерева
    // доступности в jsdom после первого показа (её уход завязан на
    // событие `transitionend`, которого jsdom не порождает), и доступное
    // имя остаётся "loadingСохранить" даже когда `saveMutation.isPending`
    // уже стал `false` в состоянии React. Это артефакт тестовой среды,
    // не поведение экрана, поэтому его не нужно точно матчить, только
    // не терять саму кнопку.
    const saveButton = screen.getByRole("button", { name: /Сохранить/ });
    expect(
      errorNode.compareDocumentPosition(saveButton) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("invalidates the cache after a successful save instead of trusting the PUT response — a subsequent GET wins", async () => {
    const year = CURRENT_YEAR;
    const fixture = fullYearFixture(year);
    let getCallCount = 0;
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "system_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === `/api/admin/production-calendar/${year}`) {
        getCallCount += 1;
        return { status: 200, body: { year, days: fixture } };
      }
      if (method === "PUT" && pathname === `/api/admin/production-calendar/${year}`) {
        // Ответ PUT намеренно НЕ совпадает с тем, что подтвердит
        // следующий GET (мок GET всегда отдаёт `fixture`) — если бы
        // экран доверял телу ответа мутации вместо инвалидации и
        // повторного запроса, он показал бы данные ИЗ ЭТОГО ответа, а не
        // то, что вернул GET.
        return { status: 200, body: { year, days: [] } };
      }
      return null;
    });

    renderApp("/admin/production-calendar");
    const user = userEvent.setup();
    await screen.findByTitle(new RegExp(`01\\.01\\.${year} — `));
    const countBeforeSave = getCallCount;

    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    await vi.waitFor(() => expect(getCallCount).toBeGreaterThan(countBeforeSave));
    // Сетка остаётся показанной по данным подтверждающего GET (`fixture`,
    // не пустой ответ PUT) — если бы клиент правил кэш телом мутации
    // вслепую, здесь оказался бы пустой год.
    expect(await screen.findByTitle(new RegExp(`01\\.01\\.${year} — `))).toBeInTheDocument();
  });
});

describe("production calendar default fill (T-M1-22, commit 4)", () => {
  it("fills a fully empty year (weekend on Sat/Sun, working otherwise) and unblocks Save", async () => {
    const year = CURRENT_YEAR;
    mockBackend({ [year]: [] });

    renderApp("/admin/production-calendar");
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "Заполнить по умолчанию" }),
    );

    const jan1Type = isWeekendDate(`${year}-01-01`) ? "Выходной" : "Рабочий";
    expect(
      await screen.findByTitle(new RegExp(`01\\.01\\.${year} — ${jan1Type}`)),
    ).toBeInTheDocument();
    // Ни одного незаданного дня не осталось — ни метки "Не задан", ни
    // самой кнопки заполнения (заполнять больше нечего), ни блокировки
    // сохранения.
    expect(screen.queryByTitle(/— Не задан/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Заполнить по умолчанию" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сохранить" })).not.toBeDisabled();
  });

  it("fills only the unset days of a partially loaded year, never touching a day already set", async () => {
    const year = CURRENT_YEAR;
    const allDates = daysInYear(year);
    const weekendDate = allDates.find((d) => isWeekendDate(d) && d !== `${year}-01-01`)!;
    const workingDate = allDates.find((d) => !isWeekendDate(d) && d !== `${year}-01-01`)!;
    // 1 января размечено ВРУЧНУЮ как праздник — заведомо не то значение,
    // которое дало бы вычисление по дню недели ни в одном году: обычный
    // рабочий день недели дал бы "working", выходной — "weekend", и обе
    // ветки отличаются от "holiday".
    mockBackend({ [year]: [{ date: `${year}-01-01`, day_type: "holiday" }] });

    renderApp("/admin/production-calendar");
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "Заполнить по умолчанию" }),
    );

    // Главный отрицательный контроль: уже заданный день (1 января,
    // "Праздник") остаётся КАК ЕСТЬ, а не переписывается вычислением по
    // дню недели.
    expect(
      await screen.findByTitle(new RegExp(`01\\.01\\.${year} — Праздник`)),
    ).toBeInTheDocument();
    // Ранее незаданные дни заполнены по дню недели.
    expect(
      screen.getByTitle(new RegExp(`${ddmmyyyy(weekendDate)} — Выходной`)),
    ).toBeInTheDocument();
    expect(
      screen.getByTitle(new RegExp(`${ddmmyyyy(workingDate)} — Рабочий`)),
    ).toBeInTheDocument();
    expect(screen.queryByTitle(/— Не задан/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сохранить" })).not.toBeDisabled();
  });

  it("does not offer the default-fill action for a year with no unset days", async () => {
    const year = CURRENT_YEAR;
    mockBackend({ [year]: fullYearFixture(year) });

    renderApp("/admin/production-calendar");
    await screen.findByTitle(new RegExp(`01\\.01\\.${year} — `));

    expect(
      screen.queryByRole("button", { name: "Заполнить по умолчанию" }),
    ).not.toBeInTheDocument();
  });
});

describe("422 validation errors (T-M2-28)", () => {
  it("save: the texts of the validation errors above the action, not the generic fallback", async () => {
    const year = CURRENT_YEAR;
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "system_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === `/api/admin/production-calendar/${year}`) {
        return { status: 200, body: { year, days: fullYearFixture(year) } };
      }
      if (method === "PUT" && pathname === `/api/admin/production-calendar/${year}`) {
        return {
          status: 422,
          body: {
            detail: [
              { loc: ["body", "days", 0, "date"], msg: "Неверная дата, нужен формат ГГГГ-ММ-ДД.", type: "date_parsing" },
              { loc: ["body", "days", 1, "day_type"], msg: "Недопустимое значение.", type: "literal_error" },
            ],
          },
        };
      }
      return null;
    });

    renderApp("/admin/production-calendar");
    const user = userEvent.setup();
    await screen.findByTitle(new RegExp(`01\\.01\\.${year} — `));
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(
      await screen.findByText("date: Неверная дата, нужен формат ГГГГ-ММ-ДД. day_type: Недопустимое значение."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Не удалось сохранить календарь.")).not.toBeInTheDocument();
  });
});


describe("the year comes from the server's business day, not the browser clock (T-M2-36)", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllEnvs();
  });

  function browserClock(nowUtc: string) {
    vi.stubEnv("TZ", "UTC");
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(nowUtc));
  }

  it("the session day is 31.12 and the browser is already in the next year: the default is the session year, the list is [year, year + 1]", async () => {
    browserClock("2027-01-01T12:00:00Z");
    const requested: number[] = [];
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "system_admin", today: "2026-12-31" }) };
      }
      const match = pathname.match(/^\/api\/admin\/production-calendar\/(\d+)$/);
      if (method === "GET" && match) {
        requested.push(Number(match[1]));
        return { status: 200, body: { year: Number(match[1]), days: [] } };
      }
      return null;
    });
    setAccessToken("token");
    const user = userEvent.setup();

    renderApp("/admin/production-calendar");
    await screen.findByText("Календарь на 2026 год не заполнен");
    await user.click(screen.getByRole("combobox", { name: "Год" }));

    expect(requested).toEqual([2026]);
    expect(await screen.findByTitle("2027")).toBeInTheDocument();
    expect(screen.queryByTitle("2025")).not.toBeInTheDocument();
  });

  it("the session day is already 01.01 and the browser is still in the old year: the default is the NEW year", async () => {
    browserClock("2026-12-31T12:00:00Z");
    mockBackend({}, { me: { today: "2027-01-01" } });
    const user = userEvent.setup();

    renderApp("/admin/production-calendar");
    await screen.findByText("Календарь на 2027 год не заполнен");
    await user.click(screen.getByRole("combobox", { name: "Год" }));

    expect(await screen.findByTitle("2028")).toBeInTheDocument();
    expect(screen.queryByTitle("2026")).not.toBeInTheDocument();
  });

  it("a session answer WITHOUT today: no browser-clock year is invented, an error with Retry instead (Ф-1)", async () => {
    browserClock("2026-06-15T12:00:00Z");
    let meCalls = 0;
    const requested: string[] = [];
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        meCalls += 1;
        const { today, ...withoutToday } = meResponse({ role: "system_admin" });
        return { status: 200, body: meCalls === 1 ? withoutToday : { ...withoutToday, today } };
      }
      if (method === "GET" && pathname.startsWith("/api/admin/production-calendar/")) {
        requested.push(pathname);
        return { status: 200, body: { year: 2026, days: [] } };
      }
      return null;
    });
    setAccessToken("token");
    const user = userEvent.setup();

    renderApp("/admin/production-calendar");
    const retry = await screen.findByRole("button", { name: "Повторить" });
    expect(requested).toEqual([]); // без дня ни одного запроса календаря, год по часам не выдуман
    expect(screen.queryByRole("combobox", { name: "Год" })).not.toBeInTheDocument();

    await user.click(retry);

    expect(await screen.findByText("Календарь на 2026 год не заполнен")).toBeInTheDocument();
    expect(meCalls).toBe(2); // «Повторить» перезапросило сессию
    expect(requested).toEqual(["/api/admin/production-calendar/2026"]); // год - из дня сессии, не июнь
  });
});
