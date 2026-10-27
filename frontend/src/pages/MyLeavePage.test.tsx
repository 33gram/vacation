import { QueryClient, QueryClientProvider, focusManager, onlineManager } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { setAccessToken } from "../app/auth";
import { SessionProvider } from "../app/SessionContext";
import { setFetchHandler } from "../test-utils/fetchMock";
import {
  balanceEntryResponse,
  balancesResponse,
  leaveTypeBalanceResponse,
  meResponse,
} from "../test-utils/fixtures";
import { renderApp } from "../test-utils/renderApp";
import { MyLeavePage } from "./MyLeavePage";

const EMPLOYEE_ID = 7;

type Reply = { status: number; body?: unknown };

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

/** Моки `/me` и остатка; `onBalances` отвечает на каждый `GET .../balances` и считает их. */
function mockBackend(
  me: Parameters<typeof meResponse>[0],
  onBalances: () => Reply | Promise<Reply> = () => ({ status: 200, body: balancesResponse() }),
) {
  const calls = { balances: 0 };
  setAccessToken("token");
  setFetchHandler((method, pathname) => {
    if (method === "GET" && pathname === "/api/auth/me") {
      return { status: 200, body: meResponse({ employee_id: EMPLOYEE_ID, ...me }) };
    }
    // Считаются обращения к остатку ЛЮБОГО сотрудника (в том числе `/api/employees/null/...`,
    // который ушёл бы без карточки), а не только своего.
    if (method === "GET" && /^\/api\/employees\/[^/]+\/balances$/.test(pathname)) {
      calls.balances += 1;
      return onBalances();
    }
    return null;
  });
  return calls;
}

/** Детерминированное «больше запросов не будет»: ни один запрос не в полёте, включая паузы
 * между автоповторами. Запрос, который вызвало действие теста (монтирование, фокус, сеть,
 * нажатие), начинается СИНХРОННО внутри этого действия, поэтому после возврата из него он уже
 * виден в `isFetching()`; ожидание его конца заменяет ожидание «на всякий случай» по таймеру,
 * которое на медленном раннере могло бы пройти на сломанном коде. */
async function settled(queryClient: QueryClient) {
  await waitFor(() => expect(queryClient.isFetching()).toBe(0));
}

/** Страница НАПРЯМУЮ, без маршрутизатора приложения и без `RoleGuard`: нужна там, где проверяется
 * сама страница и хук запроса (перерисовка родителем, размонтирование, учётная запись без
 * карточки), а не защита маршрута. */
function renderPage(
  me: Parameters<typeof meResponse>[0],
  { queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } }) } = {},
) {
  const Harness = ({ tick }: { tick: number }) => (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SessionProvider value={meResponse({ employee_id: EMPLOYEE_ID, ...me })}>
          <span data-testid="tick">{tick}</span>
          <MyLeavePage />
        </SessionProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
  const result = render(<Harness tick={1} />);
  return {
    queryClient,
    unmount: result.unmount,
    rerenderParent: (tick: number) => result.rerender(<Harness tick={tick} />),
  };
}

describe("my leave (T-M2-13, FR-BAL-002)", () => {
  it("FR-BAL-002/1: shows per leave type the balance of the current working year, its bounds, the total over all years and the event history", async () => {
    // Числа НЕ сходятся арифметически (итог не равен сумме событий): страница обязана
    // показать то, что прислал сервер, а не пересчитать.
    mockBackend(
      { role: "employee" },
      () => ({
        status: 200,
        body: balancesResponse([
          leaveTypeBalanceResponse({
            name: "Ежегодный оплачиваемый отпуск",
            balance: "18.67",
            total_balance: "46.50",
            history: [
              balanceEntryResponse({
                id: 1,
                entry_type: "accrual",
                quantity: "28.00",
                entry_date: "2026-03-10",
                actor_name: "Система",
              }),
              balanceEntryResponse({
                id: 2,
                entry_type: "manual_adjustment",
                quantity: "-3.50",
                entry_date: "2026-05-02",
                reason: "Ошибочное начисление",
                actor_name: "Иванова Анна",
              }),
            ],
          }),
        ]),
      }),
    );

    renderApp("/my-leave");

    const card = await screen.findByLabelText("Ежегодный оплачиваемый отпуск");
    expect(within(card).getByText("18,67 кал. дн.")).toBeInTheDocument();
    expect(within(card).getByText("46,5 кал. дн.")).toBeInTheDocument();
    expect(within(card).getByText("10.03.2026 — 09.03.2027")).toBeInTheDocument();
    expect(screen.getByText("Остаток на 26.10.2026")).toBeInTheDocument();
    // История: дата, тип, количество со знаком, причина, подпись автора - как пришли.
    const rows = within(card).getAllByRole("row");
    const adjustment = rows.find((row) => within(row).queryByText("Корректировка HR"));
    expect(adjustment).toBeDefined();
    expect(within(adjustment!).getByText("02.05.2026")).toBeInTheDocument();
    expect(within(adjustment!).getByText("-3,5 кал. дн.")).toBeInTheDocument();
    expect(within(adjustment!).getByText("Ошибочное начисление")).toBeInTheDocument();
    expect(within(adjustment!).getByText("Иванова Анна")).toBeInTheDocument();
    const accrual = rows.find((row) => within(row).queryByText("Начисление"));
    expect(within(accrual!).getByText("+28 кал. дн.")).toBeInTheDocument();
    expect(within(accrual!).getByText("Система")).toBeInTheDocument();
  });

  it("takes the unit of each leave type from the response, never a hardcoded word", async () => {
    mockBackend({ role: "employee" }, () => ({
      status: 200,
      body: balancesResponse([
        leaveTypeBalanceResponse({
          leave_type_id: 1,
          name: "Ежегодный оплачиваемый отпуск",
          unit: "calendar_days",
          balance: "10.00",
          total_balance: "10.00",
          history: [],
        }),
        leaveTypeBalanceResponse({
          leave_type_id: 2,
          name: "Отгул",
          unit: "hours",
          balance: "16.00",
          total_balance: "16.00",
          history: [balanceEntryResponse({ id: 5, quantity: "8.00", entry_type: "accrual" })],
        }),
        leaveTypeBalanceResponse({
          leave_type_id: 3,
          name: "Дополнительный отпуск",
          unit: "working_days",
          balance: "2.00",
          total_balance: "2.00",
          history: [],
        }),
      ]),
    }));

    renderApp("/my-leave");

    const hours = await screen.findByLabelText("Отгул");
    expect(within(hours).getAllByText("16 ч")).toHaveLength(2);
    expect(within(hours).getByText("+8 ч")).toBeInTheDocument();
    expect(within(hours).queryByText(/дн\./)).not.toBeInTheDocument();
    expect(
      within(screen.getByLabelText("Ежегодный оплачиваемый отпуск")).getAllByText("10 кал. дн."),
    ).toHaveLength(2);
    expect(
      within(screen.getByLabelText("Дополнительный отпуск")).getAllByText("2 раб. дн."),
    ).toHaveLength(2);
  });

  describe("states (ui.md §4)", () => {
    it("loading: a skeleton of rows, not a spinner", async () => {
      const gate = deferred<Reply>();
      mockBackend({ role: "employee" }, () => gate.promise);

      const { container } = renderApp("/my-leave");

      await waitFor(() => {
        expect(container.querySelector(".ant-skeleton")).not.toBeNull();
      });
      expect(container.querySelector(".ant-spin")).toBeNull();
      expect(screen.queryByText("Мой отпуск", { selector: "h3" })).not.toBeInTheDocument();

      await act(async () => {
        gate.resolve({ status: 200, body: balancesResponse() });
      });
      await screen.findByLabelText("Ежегодный оплачиваемый отпуск");
      expect(container.querySelector(".ant-skeleton")).toBeNull();
    });

    it("empty: no leave types with a balance", async () => {
      mockBackend({ role: "employee" }, () => ({ status: 200, body: balancesResponse([]) }));

      renderApp("/my-leave");

      expect(await screen.findByText("Остатков пока нет")).toBeInTheDocument();
    });

    it("error: a retry button, and no technical details", async () => {
      let attempt = 0;
      mockBackend({ role: "employee" }, () => {
        attempt += 1;
        return attempt === 1
          ? { status: 500, body: { detail: "Internal secret trace" } }
          : { status: 200, body: balancesResponse() };
      });

      renderApp("/my-leave");

      expect(await screen.findByText("Не удалось загрузить данные")).toBeInTheDocument();
      expect(screen.queryByText(/secret trace/)).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
      await screen.findByLabelText("Ежегодный оплачиваемый отпуск");
    });

    it("no access: the server's text, and the button leads to the role's start page", async () => {
      // Администратор С карточкой: «Мой отпуск» ему доступен, стартовая страница - другая,
      // поэтому переход по кнопке отличим от перезагрузки той же страницы.
      mockBackend({ role: "system_admin" }, () => ({
        status: 403,
        body: { detail: "Недостаточно прав для просмотра остатка сотрудника" },
      }));

      const { router } = renderApp("/my-leave");

      expect(
        await screen.findByText("Недостаточно прав для просмотра остатка сотрудника"),
      ).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "На доступную страницу" }));
      await waitFor(() => expect(router.state.location.pathname).toBe("/admin/ldap-settings"));
    });
  });

  describe("requests: this GET writes to the journal (T-M2-19), so they are counted", () => {
    it("sends exactly ONE request for a mount, and none for a parent re-render, focus or reconnect", async () => {
      const calls = mockBackend({ role: "employee" });
      const page = renderPage({ role: "employee" });
      await screen.findByLabelText("Ежегодный оплачиваемый отпуск");

      // Перерисовка РОДИТЕЛЕМ без размонтирования страницы - на живой странице случается чаще всего.
      page.rerenderParent(2);
      page.rerenderParent(3);
      expect(screen.getByTestId("tick")).toHaveTextContent("3");
      // Возврат на вкладку и возврат сети.
      await act(async () => {
        focusManager.setFocused(false);
        focusManager.setFocused(true);
        onlineManager.setOnline(false);
        onlineManager.setOnline(true);
      });
      await settled(page.queryClient);

      expect(calls.balances).toBe(1);
    });

    it("leaving the page and coming back is a NEW mount: a second request (harmless, T-M2-19)", async () => {
      // Это не дефект, а названное свойство: повторное начисление ничего не пишет (проверено
      // backend-тестами `accrue_leave`), второй ответ только обновляет показанное.
      const calls = mockBackend({ role: "employee" });
      const first = renderPage({ role: "employee" });
      await screen.findByLabelText("Ежегодный оплачиваемый отпуск");
      expect(calls.balances).toBe(1);

      first.unmount();
      renderPage({ role: "employee" }, { queryClient: first.queryClient });

      await screen.findByLabelText("Ежегодный оплачиваемый отпуск");
      await settled(first.queryClient);
      expect(calls.balances).toBe(2);
    });

    it("sends NO request without an employee card, even with the route guard out of the way", async () => {
      const calls = mockBackend({ role: "hr_admin", employee_id: null });

      const page = renderPage({ role: "hr_admin", employee_id: null });
      await screen.findByText("Доступ запрещён. Обратитесь к HR-администратору.");
      // Запрос, не отключённый хуком, начался бы при монтировании и был бы в полёте здесь.
      await settled(page.queryClient);

      expect(calls.balances).toBe(0);
    });

    it("sends ONE request for a mount in React's development double mount (StrictMode)", async () => {
      const calls = mockBackend({ role: "employee" });

      const { queryClient } = renderApp("/my-leave", { strictMode: true });
      await screen.findByLabelText("Ежегодный оплачиваемый отпуск");
      await settled(queryClient);

      expect(calls.balances).toBe(1);
    });

    it("does not repeat a failed request by itself: only the retry button asks again", async () => {
      const calls = mockBackend({ role: "employee" }, () => ({ status: 500, body: { detail: "x" } }));

      // Промышленное умолчание `QueryClient` (3 повтора): запрос обязан его переопределить.
      const { queryClient } = renderApp("/my-leave", { retry: 3 });
      // Ошибка показывается после ПОСЛЕДНЕЙ попытки: при трёх автоповторах их бы уже посчитали.
      await screen.findByText("Не удалось загрузить данные");
      await settled(queryClient);

      expect(calls.balances).toBe(1);
    });

    it("a burst of clicks on the retry button sends one request (the button's behaviour, not a check of the cancelRefetch flag)", async () => {
      const retryGate = deferred<Reply>();
      let attempt = 0;
      const calls = mockBackend({ role: "employee" }, () => {
        attempt += 1;
        return attempt === 1 ? { status: 500, body: { detail: "x" } } : retryGate.promise;
      });

      const { queryClient } = renderApp("/my-leave");
      const button = await screen.findByRole("button", { name: "Повторить" });
      expect(calls.balances).toBe(1);

      fireEvent.click(button);
      fireEvent.click(button);
      fireEvent.click(button);
      // Все запросы от нажатий уже начались (синхронно) и висят на шлюзе: отпускаем его и
      // считаем после того, как больше ничего не в полёте.
      await act(async () => {
        retryGate.resolve({ status: 200, body: balancesResponse() });
      });
      await screen.findByLabelText("Ежегодный оплачиваемый отпуск");
      await settled(queryClient);
      expect(calls.balances).toBe(2);
    });
  });
});

describe("self-service follows the employee card, not the role (requirements §7, §13 п. 22)", () => {
  it.each(["employee", "hr_admin", "accountant", "system_admin"])(
    "shows the «Мой отпуск» menu item to %s WITH a card",
    async (role) => {
      mockBackend({ role });

      renderApp("/my-leave");

      await screen.findByLabelText("Ежегодный оплачиваемый отпуск");
      expect(screen.getByRole("menuitem", { name: "Мой отпуск" })).toBeInTheDocument();
    },
  );

  it.each(["employee", "hr_admin", "accountant", "system_admin"])(
    "has no «Мой отпуск» item for %s WITHOUT a card",
    async (role) => {
      const calls = mockBackend({ role, employee_id: null });

      renderApp("/");

      // Дожидаемся оболочки: у роли без карточки есть либо другой раздел, либо заглушка.
      await screen.findByRole("button", { name: "Выйти" });
      expect(screen.queryByRole("menuitem", { name: "Мой отпуск" })).not.toBeInTheDocument();
      expect(calls.balances).toBe(0);
    },
  );

  it("a direct URL without a card gives «no access», not a broken page, and asks nothing", async () => {
    const calls = mockBackend({ role: "hr_admin", employee_id: null });

    renderApp("/my-leave");

    expect(
      await screen.findByText("Доступ запрещён. Обратитесь к HR-администратору."),
    ).toBeInTheDocument();
    expect(calls.balances).toBe(0);
    expect(screen.getByRole("button", { name: "На доступную страницу" })).toBeInTheDocument();
  });
});

describe("start page (ui.md §2.3)", () => {
  it.each([
    ["employee", 7, "/my-leave"],
    ["hr_admin", 7, "/my-leave"],
    ["accountant", 7, "/my-leave"],
    ["system_admin", 7, "/admin/ldap-settings"],
    ["system_admin", null, "/admin/ldap-settings"],
    ["hr_admin", null, "/employees"],
  ])("%s with employee_id=%s starts on %s", async (role, employeeId, expectedPath) => {
    mockBackend({ role, employee_id: employeeId });

    const { router } = renderApp("/");

    await waitFor(() => expect(router.state.location.pathname).toBe(expectedPath));
  });

  it.each(["employee", "accountant"])(
    "keeps the stub only for %s WITHOUT a card (no section is available)",
    async (role) => {
      mockBackend({ role, employee_id: null });

      renderApp("/");

      expect(await screen.findByText("Разделы этой роли ещё не готовы")).toBeInTheDocument();
    },
  );
});
