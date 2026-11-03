import { fireEvent, screen } from "@testing-library/react";
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

// rc-tree (antd Tree) отслеживает перетаскиваемый узел сама, по тому,
// какой DOM-элемент получил `dragstart` — не по `event.dataTransfer`
// (`TreeNode.onDragStart` читает `dataTransfer.setData` в try/catch,
// молча проглатывая исключение, если его нет). jsdom (30.1.1, эта
// репа) не реализует `DataTransfer`/`DragEvent` вовсе — проверено
// прогоном (`typeof new (require('jsdom').JSDOM）().window.
// DataTransfer === "undefined"`) — поэтому `dataTransfer` в вызовах
// `fireEvent` ниже не передаётся, простого `Event`-фолбэка
// `fireEvent`'а достаточно.
function findTreeNode(text: RegExp | string): HTMLElement {
  const titleElement = screen.getByText(text);
  const treeNode = titleElement.closest(".ant-tree-treenode");
  if (!(treeNode instanceof HTMLElement)) {
    throw new Error(`Tree node element not found for ${String(text)}`);
  }
  return treeNode;
}

function dragOnto(source: HTMLElement, target: HTMLElement): void {
  fireEvent.dragStart(source);
  fireEvent.dragEnter(target);
  fireEvent.dragOver(target);
  fireEvent.drop(target);
  fireEvent.dragEnd(source);
}

/** Даёт асинхронному коду (микрозадачи мутации, сам мок `fetch`) шанс
 * выполниться, прежде чем проверять «ничего не произошло» — синхронная
 * проверка сразу после `dragOnto` не отличила бы «заблокировано» от
 * «ещё не успело», отсечка при этом осталась бы непроверенной. */
async function settle(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 50));
}

describe("departments reparenting by drag and drop (T-M1-20, FR-EMP-002)", () => {
  it("moving a node onto another sends all three fields — new parent_id, unchanged name/head", async () => {
    const departmentA = departmentResponse({ id: 1, name: "Подразделение A", parent_id: null, head_employee_id: null });
    const departmentB = departmentResponse({ id: 2, name: "Подразделение B", parent_id: null, head_employee_id: null });
    const child = departmentResponse({ id: 3, name: "Ребёнок", parent_id: 1, head_employee_id: 9 });
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
        return { status: 200, body: [departmentA, departmentB, child] };
      }
      if (method === "PUT" && pathname === "/api/departments/3") {
        putBody = JSON.parse(init.body as string) as Record<string, unknown>;
        return { status: 200, body: { ...child, parent_id: putBody.parent_id } };
      }
      return null;
    });

    renderApp("/departments");
    await screen.findByText(/Ребёнок/);

    const sourceNode = findTreeNode(/Ребёнок — /);
    const targetNode = findTreeNode(/Подразделение B — /);
    dragOnto(sourceNode, targetNode);

    await vi.waitFor(() => expect(putBody).not.toBeNull());
    expect(putBody).toEqual({ name: "Ребёнок", parent_id: 2, head_employee_id: 9 });
  });

  it("a server rejection (cycle) shows the server's own text and reverts the tree to its pre-move state", async () => {
    // Та же топология, что доказанно работает в предыдущем тесте:
    // перетаскиваемый узел — ВЛОЖЕННЫЙ (не корень), цель — отдельный
    // корень. Важно для устойчивости теста к тому, как именно jsdom без
    // реальных layout-размеров (`getBoundingClientRect` возвращает
    // нули) сосчитает геометрию сброса: воспроизведено прогоном, что
    // сброс КОРНЯ на КОРЕНЬ в таких условиях у `rc-tree` интерпретиру-
    // ется как вставка В ЗАЗОР на том же уровне (`dropToGap`), где
    // новый `parent_id` цели — `null` — и совпадает со starting-значе-
    // нием переносимого корня, из-за чего тест ничего не доказывает.
    // У вложенного узла исходный `parent_id` (1) НЕ совпадает ни с
    // одним из двух возможных исходов сброса на другой корень (id цели
    // ИЛИ `null`), поэтому тест устойчив к обеим геометриям.
    //
    // Настоящий цикл ("подразделение — потомок собственного потомка")
    // проверяет СЕРВЕР, не тестовый стенд: то, что здесь отклоняется
    // именно этот запрос текстом про цикл, — решение мока, а не
    // топология дерева в тесте. Это не ослабляет тест: проверяется
    // механизм отката при ЛЮБОМ отказе сервера, конкретную причину
    // (настоящий цикл) проверяет бэкенд отдельно (правило 5).
    const root = departmentResponse({ id: 1, name: "Родитель", parent_id: null });
    const nested = departmentResponse({ id: 2, name: "Вложенный", parent_id: 1 });
    const otherRoot = departmentResponse({ id: 3, name: "Другой корень", parent_id: null });
    setAccessToken("token");

    // Второй GET /api/departments (вызванный `invalidateQueries` в
    // `onSettled` ПОСЛЕ отклонённого PUT) держится на гейте — сервер в
    // реальности вернул бы то же самое исходное состояние (он никогда
    // не принимал отклонённую правку), и без отдельного отката в
    // `onError` сам этот рефетч, рано или поздно, тоже стёр бы
    // оптимистичную ложь — тест, который проверяет кэш ПОСЛЕ того, как
    // этот GET успел разрешиться, ничего не доказывает: он прошёл бы и
    // без отката, откат в `onError` при этом можно вырезать незаметно
    // (проверено прогоном ниже). Гейт (тот же приём, что и `meGate` в
    // `authShell.test.tsx`, T-M1-53) держит именно ЭТО окно — между
    // отказом PUT и завершением инвалидации — под наблюдением.
    let departmentsGetCount = 0;
    const secondGet: { resolve: (() => void) | null } = { resolve: null };
    const secondGetGate = new Promise<void>((resolve) => {
      secondGet.resolve = resolve;
    });
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/employees") {
        return { status: 200, body: [] };
      }
      if (method === "GET" && pathname === "/api/departments") {
        departmentsGetCount += 1;
        if (departmentsGetCount === 1) {
          return { status: 200, body: [root, nested, otherRoot] };
        }
        return secondGetGate.then(() => ({ status: 200, body: [root, nested, otherRoot] }));
      }
      if (method === "PUT" && pathname === "/api/departments/2") {
        return {
          status: 400,
          body: { detail: "Подразделение не может стать потомком собственного потомка" },
        };
      }
      return null;
    });

    const { queryClient } = renderApp("/departments");
    await screen.findByText(/Вложенный/);

    const sourceNode = findTreeNode(/Вложенный — /);
    const targetNode = findTreeNode(/Другой корень — /);
    dragOnto(sourceNode, targetNode);

    // К моменту, когда текст ошибки появился на экране, `onError` уже
    // отработал целиком (откат кэша — первая строка того же
    // синхронного обработчика, до `setReparentError`) — а второй GET
    // всё ещё висит на гейте, ничего ещё не перезаписал сам.
    expect(
      await screen.findByText("Подразделение не может стать потомком собственного потомка"),
    ).toBeInTheDocument();

    // Главный отрицательный контроль: ИМЕННО в этом окне, до открытия
    // гейта, кэш обязан УЖЕ быть откачен — единственное, что могло его
    // откатить здесь, это `onError`, а не рефетч (он ещё не завершён).
    const duringGate = queryClient.getQueryData<{ id: number; parent_id: number | null }[]>([
      "departments",
    ]);
    expect(duringGate?.find((d) => d.id === 2)?.parent_id).toBe(1);

    secondGet.resolve?.();
    await vi.waitFor(() => {
      const afterRefetch = queryClient.getQueryData<{ id: number; parent_id: number | null }[]>([
        "departments",
      ]);
      expect(afterRefetch?.find((d) => d.id === 2)?.parent_id).toBe(1);
    });
  });

  it("a second drag started before the first PUT resolves does not send a second request, and dragging works again once it settles", async () => {
    // Тот же приём с гейтом, что и выше — но на этот раз держим сам
    // первый PUT, а не последующий GET: окно гонки, которое проверяет
    // этот тест, — между первым `drop` и ответом сервера на его PUT, а
    // не между отказом и рефетчем.
    const root = departmentResponse({ id: 1, name: "Родитель", parent_id: null });
    const nested = departmentResponse({ id: 2, name: "Вложенный", parent_id: 1 });
    const otherRoot = departmentResponse({ id: 3, name: "Другой корень", parent_id: null });
    // Независимая пара для проверки «перетаскивание снова работает» —
    // НЕ те же узлы, что участвуют в заблокированном первом переносе:
    // после его успешного завершения "Вложенный" физически переезжает
    // под "Другой корень" в дереве, и его новая позиция зависит от
    // состояния сворачивания узла-получателя — не то, что здесь
    // проверяется. Независимая пара остаётся на месте независимо от
    // первого переноса.
    const nested2 = departmentResponse({ id: 4, name: "Вложенный2", parent_id: 1 });
    const otherRoot2 = departmentResponse({ id: 5, name: "Другой корень2", parent_id: null });
    setAccessToken("token");

    let putCountForNode2 = 0;
    let putBodyForNode4: Record<string, unknown> | null = null;
    const firstPut: { resolve: (() => void) | null } = { resolve: null };
    const firstPutGate = new Promise<void>((resolve) => {
      firstPut.resolve = resolve;
    });
    setFetchHandler((method, pathname, init) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/employees") {
        return { status: 200, body: [] };
      }
      if (method === "GET" && pathname === "/api/departments") {
        return { status: 200, body: [root, nested, otherRoot, nested2, otherRoot2] };
      }
      if (method === "PUT" && pathname === "/api/departments/2") {
        putCountForNode2 += 1;
        return firstPutGate.then(() => ({ status: 200, body: { ...nested, parent_id: 3 } }));
      }
      if (method === "PUT" && pathname === "/api/departments/4") {
        putBodyForNode4 = JSON.parse(init.body as string) as Record<string, unknown>;
        return { status: 200, body: { ...nested2, parent_id: putBodyForNode4.parent_id } };
      }
      return null;
    });

    renderApp("/departments");
    await screen.findByText(/Вложенный — /);

    const sourceNode = findTreeNode(/^Вложенный — /);
    const targetNode = findTreeNode(/Другой корень — /);

    dragOnto(sourceNode, targetNode);
    await vi.waitFor(() => expect(putCountForNode2).toBe(1));

    // Главный отрицательный контроль: пока первый PUT висит на гейте,
    // ЛЮБОЕ новое перетаскивание — не обязательно того же узла на ту же
    // цель — не должно отправлять запрос вовсе. Независимая пара, а не
    // повторный бросок исходной: у самого исходного узла к этому
    // моменту уже другая позиция в DOM (оптимистичное обновление кэша
    // из `onMutate` уже отразилось в дереве), и попытка «перетащить его
    // ещё раз» перестаёт быть чистой проверкой отсечки — она либо не
    // находит прежний DOM-узел, либо натыкается на смену позиции по
    // причине, не имеющей отношения к `isPending`. Независимая пара
    // такого искажения не вносит. Проверка «ничего не произошло»
    // обязана дать асинхронному коду шанс произойти, а не читать
    // результат сразу же (тот же тик, где PUT ещё физически не мог быть
    // отправлен, — с настоящей отсечкой ИЛИ без неё, отличить одно от
    // другого было бы нечем).
    dragOnto(findTreeNode(/^Вложенный2 — /), findTreeNode(/Другой корень2 — /));
    await settle();
    expect(putBodyForNode4).toBeNull();

    firstPut.resolve?.();
    await vi.waitFor(() => expect(isDraggable(findTreeNode(/^Вложенный2 — /))).toBe(true));

    // Независимая пара — перетаскивание снова работает. Проверяем по
    // отправленному телу PUT (как и остальные тесты этого файла), не
    // по состоянию кэша после отклика: мок GET статичен и не отражает
    // ни одной мутации, поэтому рефетч, вызванный ЛЮБОЙ инвалидацией
    // (в т.ч. от этого же переноса), перезаписывает кэш обратно
    // «немутированными» данными и ничего не доказывает — особенность
    // мока, а не гонка в продакшен-коде: настоящий сервер отдал бы уже
    // актуальное состояние.
    dragOnto(findTreeNode(/^Вложенный2 — /), findTreeNode(/Другой корень2 — /));
    await vi.waitFor(() => expect(putBodyForNode4).not.toBeNull());
    expect(putBodyForNode4).toEqual({ name: "Вложенный2", parent_id: 5, head_employee_id: null });
  });
});

/** `draggable="false"` на узле, пока перенос ещё не разрешился (см.
 * тест выше) — этот помощник ждёт, что атрибут вернулся к `"true"`
 * (антд включает перетаскивание обратно), не полагаясь на побочные
 * эффекты вроде повторного рендера дерева. */
function isDraggable(node: HTMLElement): boolean {
  return node.getAttribute("draggable") === "true";
}

describe("422 validation errors (T-M2-28)", () => {
  it("create: the window has one field, its validation error is shown in the window, not the fallback", async () => {
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/departments") {
        return { status: 200, body: [departmentResponse({ id: 1, name: "Существующий отдел", parent_id: null })] };
      }
      if (method === "GET" && pathname === "/api/employees") {
        return { status: 200, body: [] };
      }
      if (method === "POST" && pathname === "/api/departments") {
        return {
          status: 422,
          body: {
            detail: [
              { loc: ["body", "name"], msg: "Слишком длинное значение: не более 200 символов.", type: "string_too_long" },
            ],
          },
        };
      }
      return null;
    });

    renderApp("/departments");
    const user = userEvent.setup();
    await screen.findByText(/Существующий отдел/);
    await user.click(screen.getByRole("button", { name: "Добавить подразделение" }));
    await user.type(screen.getByLabelText("Название"), "Ещё один отдел");
    await user.click(screen.getByRole("button", { name: "Создать" }));

    const message = await screen.findByText("Слишком длинное значение: не более 200 символов.");
    expect(message.closest(".ant-alert")).not.toBeNull();
    expect(screen.queryByText("Не удалось создать подразделение.")).not.toBeInTheDocument();
  });

  it("assign head: the error of head_employee_id is shown under the head field", async () => {
    const department = departmentResponse({ id: 1, name: "Отдел", parent_id: 5, head_employee_id: null });
    const activeEmployee = employeeResponse({ id: 1, full_name: "Иванов Иван Иванович" });
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
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
        return {
          status: 422,
          body: {
            detail: [
              { loc: ["body", "head_employee_id"], msg: "Значение должно быть целым числом.", type: "int_parsing" },
            ],
          },
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

    const message = await screen.findByText("Значение должно быть целым числом.");
    expect(screen.getByLabelText("Руководитель").closest(".ant-form-item")).toContainElement(message);
  });
});
