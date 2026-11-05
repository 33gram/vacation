import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { setAccessToken } from "../../app/auth";
import { setFetchHandler } from "../../test-utils/fetchMock";
import { ldapSettingsResponse, meResponse } from "../../test-utils/fixtures";
import { renderApp } from "../../test-utils/renderApp";

function mockBackend(ldapSettings: ReturnType<typeof ldapSettingsResponse> | null, getStatus = 200) {
  setAccessToken("token");
  setFetchHandler((method, pathname) => {
    if (method === "GET" && pathname === "/api/auth/me") {
      return { status: 200, body: meResponse({ role: "system_admin", must_change_password: false }) };
    }
    if (method === "GET" && pathname === "/api/admin/ldap-settings") {
      if (getStatus !== 200) {
        return { status: getStatus, body: { detail: "internal error" } };
      }
      return { status: 200, body: ldapSettings };
    }
    return null;
  });
}

describe("LDAP settings, read only (T-M1-21, FR-ADMIN-005)", () => {
  it("shows the loading state, then the form once data arrives", async () => {
    mockBackend(ldapSettingsResponse());

    renderApp("/admin/ldap-settings");

    expect(
      await screen.findByRole("heading", { name: "Администрирование → LDAP" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Адрес сервера")).toHaveValue("ldap://dc.company.local");
  });

  it("shows an error state with Retry when the request fails, and recovers after retry", async () => {
    let callCount = 0;
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "system_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/admin/ldap-settings") {
        callCount += 1;
        if (callCount === 1) {
          return { status: 500, body: { detail: "internal error" } };
        }
        return { status: 200, body: null };
      }
      return null;
    });

    renderApp("/admin/ldap-settings");

    expect(await screen.findByText("Не удалось загрузить данные")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Повторить" }));

    expect(await screen.findByText("Подключение к LDAP не настроено")).toBeInTheDocument();
  });

  it("shows the empty state (not an empty form or an error) when settings were never saved", async () => {
    mockBackend(null);

    renderApp("/admin/ldap-settings");

    expect(await screen.findByText("Подключение к LDAP не настроено")).toBeInTheDocument();
    expect(screen.queryByText("Не удалось загрузить данные")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Адрес сервера")).not.toBeInTheDocument();
  });

  it("reveals a blank form with server defaults from the empty state's primary action", async () => {
    mockBackend(null);

    renderApp("/admin/ldap-settings");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Настроить подключение" }));

    expect(await screen.findByLabelText("Адрес сервера")).toHaveValue("");
    expect(screen.getByLabelText("Порт")).toHaveValue("389");
    expect(screen.getByLabelText("Пароль служебной записи")).toHaveValue("");
  });

  it("renders every saved field, in two columns, and formats the recorded check date as ДД.ММ.ГГГГ ЧЧ:ММ", async () => {
    mockBackend(
      ldapSettingsResponse({
        last_tested_at: "2026-10-01T09:05:30",
        last_test_status: "success",
      }),
    );

    renderApp("/admin/ldap-settings");

    expect(await screen.findByLabelText("Адрес сервера")).toHaveValue("ldap://dc.company.local");
    expect(screen.getByLabelText("Порт")).toHaveValue("389");
    expect(screen.getByLabelText("Base DN")).toHaveValue("dc=company,dc=local");
    expect(screen.getByLabelText("Bind DN (служебная учётная запись)")).toHaveValue(
      "cn=svc-vacation,dc=company,dc=local",
    );
    expect(screen.getByLabelText("Атрибут сопоставления пользователя")).toHaveValue("mail");
    expect(
      screen.getByText("Последняя зафиксированная проверка: 01.10.2026 09:05 — Успешно"),
    ).toBeInTheDocument();

    // Два столбца (`ui.md` §3, «Форма настроек») — поля не перемешаны
    // между ними как попало: левый содержит адрес/порт/base DN/атрибут,
    // правый — bind DN/пароль/переключатель. `data-testid`, не `.ant-col`
    // (у `Form.Item` этот класс тоже есть внутри — глобальный запрос по
    // нему поймал бы все 16 антд-колонок на странице, не только эти две).
    const targetColumn = screen.getByTestId("ldap-settings-column-target");
    const credentialsColumn = screen.getByTestId("ldap-settings-column-credentials");
    expect(within(targetColumn).getByLabelText("Адрес сервера")).toBeInTheDocument();
    expect(within(targetColumn).getByLabelText("Base DN")).toBeInTheDocument();
    expect(
      within(credentialsColumn).getByLabelText("Bind DN (служебная учётная запись)"),
    ).toBeInTheDocument();
    expect(within(credentialsColumn).getByLabelText("Пароль служебной записи")).toBeInTheDocument();
  });

  // FR-ADMIN-005/2, the other half ("явно сохранены с предупреждением"):
  // this is the warning shown while a saved config hasn't been
  // confirmed - the backend saves unconditionally (T-M1-16), the UI's
  // own job is to make that state visible rather than look verified.
  it("shows 'ещё не проверялось' when the saved record has never been tested", async () => {
    mockBackend(ldapSettingsResponse({ last_tested_at: null, last_test_status: null }));

    renderApp("/admin/ldap-settings");

    expect(
      await screen.findByText("Последняя зафиксированная проверка: Ещё не проверялось"),
    ).toBeInTheDocument();
  });

  describe("the stored password is never shown (danger 1)", () => {
    it("renders the password field empty for a saved record, with no stored value anywhere on the page", async () => {
      mockBackend(ldapSettingsResponse());

      renderApp("/admin/ldap-settings");

      const passwordField = await screen.findByLabelText("Пароль служебной записи");
      expect(passwordField).toHaveValue("");
      // Отрицательный контроль на саму идею теста: сервер в этом тесте
      // НИКОГДА не присылал пароль (`ldapSettingsResponse()` не содержит
      // поля `bind_password` вовсе, как и настоящий `LdapSettingsOut`) —
      // так что искать "утёкшее" значение негде в принципе; проверка
      // пустого поля значима именно потому, что это ЕДИНСТВЕННОЕ
      // возможное состояние поля при любом сохранённом пароле.
      expect(screen.getByText(/Пустое поле — оставить прежний пароль/)).toBeInTheDocument();
    });

    it("hints that a password is required to test, not that a previous one is kept, when settings were never saved (PR #159 review)", async () => {
      mockBackend(null);

      renderApp("/admin/ldap-settings");
      const user = userEvent.setup();
      await user.click(await screen.findByRole("button", { name: "Настроить подключение" }));

      // При первичной настройке "прежнего" пароля нет — подсказка не
      // обязана (и не должна) обещать его сохранение.
      expect(screen.queryByText(/оставить прежний пароль/)).not.toBeInTheDocument();
      expect(
        await screen.findByText(/без пароля проверить подключение не получится/),
      ).toBeInTheDocument();
    });
  });
});

describe("saving the connection parameters (T-M1-21, commit 2)", () => {
  function savedFixture() {
    return ldapSettingsResponse({
      last_tested_at: "2026-09-01T10:00:00",
      last_test_status: "success",
    });
  }

  it("omits bind_password from the PUT body when the field is left empty (danger 2)", async () => {
    const saved = savedFixture();
    type PutBody = Record<string, unknown>;
    let putBody: PutBody | null = null;
    setAccessToken("token");
    setFetchHandler((method, pathname, init) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "system_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/admin/ldap-settings") {
        return { status: 200, body: saved };
      }
      if (method === "PUT" && pathname === "/api/admin/ldap-settings") {
        putBody = JSON.parse(init.body as string) as PutBody;
        return { status: 200, body: { ...saved, last_tested_at: null, last_test_status: null } };
      }
      return null;
    });

    renderApp("/admin/ldap-settings");
    await screen.findByLabelText("Адрес сервера");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Сохранить/ }));

    await vi.waitFor(() => expect(putBody).not.toBeNull());
    const sentBody = putBody!;
    // Главный отрицательный контроль (проверено прогоном до реализации,
    // см. тело коммита): ключ обязан ОТСУТСТВОВАТЬ, а не быть `""`.
    expect("bind_password" in sentBody).toBe(false);
  });

  it("shows the server's own error text on a failed save, above the button", async () => {
    const saved = savedFixture();
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "system_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/admin/ldap-settings") {
        return { status: 200, body: saved };
      }
      if (method === "PUT" && pathname === "/api/admin/ldap-settings") {
        // Реальный путь ошибки (`main.py`'s app-level IntegrityError
        // safety net, не выдуманный текст): 409 с этим же текстом.
        return { status: 409, body: { detail: "Конфликт с уже существующими данными. Попробуйте ещё раз." } };
      }
      return null;
    });

    renderApp("/admin/ldap-settings");
    await screen.findByLabelText("Адрес сервера");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Сохранить/ }));

    const errorNode = await screen.findByText(
      "Конфликт с уже существующими данными. Попробуйте ещё раз.",
    );
    // `ui.md` §7, «Место ошибки»: над кнопками действия — проверено через
    // положение узлов (`compareDocumentPosition`), тот же способ, что
    // `PR #158 review` закрепило для производственного календаря.
    const saveButton = screen.getByRole("button", { name: /Сохранить/ });
    expect(
      errorNode.compareDocumentPosition(saveButton) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("no longer shows a stale 'successfully tested' marker after saving (danger 3)", async () => {
    const saved = savedFixture();
    let getCount = 0;
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "system_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/admin/ldap-settings") {
        getCount += 1;
        if (getCount === 1) {
          return { status: 200, body: saved };
        }
        // Сервер уже обнулил last_tested_at/last_test_status при PUT
        // (upsert_ldap_settings) — второй GET это подтверждает.
        return { status: 200, body: { ...saved, last_tested_at: null, last_test_status: null } };
      }
      if (method === "PUT" && pathname === "/api/admin/ldap-settings") {
        // Ответ PUT намеренно совпадает со СТАРЫМ состоянием, а не с тем,
        // что вернёт следующий GET — если бы экран доверял телу ответа
        // мутации вместо инвалидации и повторного запроса, отметка
        // "Успешно" осталась бы на экране.
        return { status: 200, body: saved };
      }
      return null;
    });

    renderApp("/admin/ldap-settings");
    await screen.findByText("Последняя зафиксированная проверка: 01.09.2026 10:00 — Успешно");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Сохранить/ }));

    await screen.findByText("Последняя зафиксированная проверка: Ещё не проверялось");
    expect(screen.queryByText(/Успешно/)).not.toBeInTheDocument();
  });

  it("invalidates ['me'] too, so the embedded-mode banner stays in sync after saving (danger 3)", async () => {
    const saved = savedFixture();
    let meCallCount = 0;
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        meCallCount += 1;
        return {
          status: 200,
          body: meResponse({
            role: "system_admin",
            must_change_password: false,
            embedded_mode: meCallCount === 1,
          }),
        };
      }
      if (method === "GET" && pathname === "/api/admin/ldap-settings") {
        return { status: 200, body: saved };
      }
      if (method === "PUT" && pathname === "/api/admin/ldap-settings") {
        return { status: 200, body: saved };
      }
      return null;
    });

    renderApp("/admin/ldap-settings");
    await screen.findByText(/Режим внедрения без LDAP/);
    await screen.findByLabelText("Адрес сервера");
    const callsBeforeSave = meCallCount;

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Сохранить/ }));

    await vi.waitFor(() => expect(meCallCount).toBeGreaterThan(callsBeforeSave));
    expect(screen.queryByText(/Режим внедрения без LDAP/)).not.toBeInTheDocument();
  });
});

describe("testing the connection without saving (T-M1-21, commit 3)", () => {
  function savedFixture() {
    return ldapSettingsResponse({
      last_tested_at: "2026-09-01T10:00:00",
      last_test_status: "success",
    });
  }

  it("explains which case is active: saved record by default, draft once a field changes", async () => {
    mockBackend(savedFixture());

    renderApp("/admin/ldap-settings");
    await screen.findByText(
      "Будет проверена сохранённая запись; результат зафиксируется на сервере.",
    );

    const user = userEvent.setup();
    await user.clear(screen.getByLabelText("Порт"));
    await user.type(screen.getByLabelText("Порт"), "636");

    expect(
      await screen.findByText(
        "Будут проверены введённые, но ещё не сохранённые значения — само сохранение не выполняется.",
      ),
    ).toBeInTheDocument();
  });

  it("sends an empty body when the form has no unsaved changes, verifying the saved record", async () => {
    const saved = savedFixture();
    type TestBody = Record<string, unknown>;
    let testBody: TestBody | undefined;
    // Флаг, не отсутствующая ветка: как и в тесте ниже
    // ("testing draft parameters never triggers a save request"),
    // react-query гасит отказ мутации внутри себя (`onError`), так что
    // ошибка диспетчера мока на незаданном пути НЕ обрушивает тест сама
    // по себе, если ничего в тесте её не ждёт (`PR #159 review`:
    // предыдущая версия этого теста полагалась именно на это и
    // комментарий утверждал несуществующую защиту).
    let putWasCalled = false;
    setAccessToken("token");
    setFetchHandler((method, pathname, init) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "system_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/admin/ldap-settings") {
        return { status: 200, body: saved };
      }
      if (method === "POST" && pathname === "/api/admin/ldap-settings/test") {
        testBody = JSON.parse(init.body as string) as TestBody;
        return { status: 200, body: { success: true, detail: "Соединение установлено" } };
      }
      if (method === "PUT" && pathname === "/api/admin/ldap-settings") {
        putWasCalled = true;
        return { status: 200, body: saved };
      }
      return null;
    });

    renderApp("/admin/ldap-settings");
    await screen.findByLabelText("Адрес сервера");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Тестовое подключение" }));

    await vi.waitFor(() => expect(testBody).toBeDefined());
    expect(testBody).toEqual({});
    expect(await screen.findByText("Соединение установлено")).toBeInTheDocument();
    expect(putWasCalled).toBe(false);
  });

  it("sends the filled draft fields (not an empty body) when the form has unsaved changes", async () => {
    const saved = savedFixture();
    type TestBody = Record<string, unknown>;
    let testBody: TestBody | undefined;
    setAccessToken("token");
    setFetchHandler((method, pathname, init) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "system_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/admin/ldap-settings") {
        return { status: 200, body: saved };
      }
      if (method === "POST" && pathname === "/api/admin/ldap-settings/test") {
        testBody = JSON.parse(init.body as string) as TestBody;
        return { status: 200, body: { success: true, detail: "Соединение установлено" } };
      }
      return null;
    });

    renderApp("/admin/ldap-settings");
    await screen.findByLabelText("Адрес сервера");
    const user = userEvent.setup();
    await user.clear(screen.getByLabelText("Порт"));
    await user.type(screen.getByLabelText("Порт"), "636");
    await user.click(screen.getByRole("button", { name: "Тестовое подключение" }));

    await vi.waitFor(() => expect(testBody).toBeDefined());
    expect(testBody).not.toEqual({});
    expect(testBody?.server_url).toBe(saved.server_url);
    expect(testBody?.port).toBe(636);
    // Пустое поле пароля в черновике не означает "" — это отсутствие
    // ключа, то же правило, что и у сохранения (опасность 2 применена и
    // здесь: сервер должен подставить сохранённый пароль, а не получить
    // пустую строку).
    expect("bind_password" in (testBody ?? {})).toBe(false);
  });

  it("shows a failed test's own result text without touching the recorded check", async () => {
    const saved = savedFixture();
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "system_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/admin/ldap-settings") {
        return { status: 200, body: saved };
      }
      if (method === "POST" && pathname === "/api/admin/ldap-settings/test") {
        return { status: 200, body: { success: false, detail: "Неверные учётные данные" } };
      }
      return null;
    });

    renderApp("/admin/ldap-settings");
    await screen.findByLabelText("Адрес сервера");
    const user = userEvent.setup();
    await user.clear(screen.getByLabelText("Порт"));
    await user.type(screen.getByLabelText("Порт"), "636");
    await user.click(screen.getByRole("button", { name: "Тестовое подключение" }));

    expect(await screen.findByText("Неверные учётные данные")).toBeInTheDocument();
  });

  it("testing draft parameters never triggers a save request (ui.md §3: the test button doesn't save)", async () => {
    const saved = savedFixture();
    // Флаг, а не "нет ветки для PUT": react-query гасит отказ мутации
    // внутри себя (вызывается только `onError` обработчика), так что
    // брошенная диспетчером мока ошибка на незаданном пути НЕ обрушивает
    // тест сама по себе, если ничего в тесте её не ждёт, — отрицательный
    // контроль обязан проверять факт вызова PUT явно, а не полагаться на
    // побочный эффект отсутствующей ветки (проверено прогоном: с
    // обработчиком `handleTest`, вызывающим и `saveMutation.mutate`,
    // предыдущая версия этого теста — без флага — оставалась зелёной).
    let putWasCalled = false;
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "system_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/admin/ldap-settings") {
        return { status: 200, body: saved };
      }
      if (method === "POST" && pathname === "/api/admin/ldap-settings/test") {
        return { status: 200, body: { success: true, detail: "Соединение установлено" } };
      }
      if (method === "PUT" && pathname === "/api/admin/ldap-settings") {
        putWasCalled = true;
        return { status: 200, body: saved };
      }
      return null;
    });

    renderApp("/admin/ldap-settings");
    await screen.findByLabelText("Адрес сервера");
    const user = userEvent.setup();
    await user.clear(screen.getByLabelText("Порт"));
    await user.type(screen.getByLabelText("Порт"), "636");
    await user.click(screen.getByRole("button", { name: "Тестовое подключение" }));

    expect(await screen.findByText("Соединение установлено")).toBeInTheDocument();
    expect(putWasCalled).toBe(false);
  });

  it("clears the one-off test result on any further field edit (PR #159 review)", async () => {
    const saved = savedFixture();
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "system_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/admin/ldap-settings") {
        return { status: 200, body: saved };
      }
      if (method === "POST" && pathname === "/api/admin/ldap-settings/test") {
        return { status: 200, body: { success: true, detail: "Соединение установлено" } };
      }
      return null;
    });

    renderApp("/admin/ldap-settings");
    await screen.findByLabelText("Адрес сервера");
    const user = userEvent.setup();
    // Черновая проверка (поле уже изменено) — результат появляется.
    await user.clear(screen.getByLabelText("Порт"));
    await user.type(screen.getByLabelText("Порт"), "636");
    await user.click(screen.getByRole("button", { name: "Тестовое подключение" }));
    expect(await screen.findByText("Соединение установлено")).toBeInTheDocument();

    // Дальнейшая правка ЛЮБОГО поля — отметка больше не описывает то, что
    // сейчас в форме, и обязана исчезнуть, хотя её никто не трогал
    // напрямую (не сохранение, не новая проверка).
    await user.clear(screen.getByLabelText("Base DN"));
    await user.type(screen.getByLabelText("Base DN"), "dc=other,dc=local");

    expect(screen.queryByText("Соединение установлено")).not.toBeInTheDocument();
    // Зафиксированный результат последней проверки (состояние системы,
    // не разовое действие) при этом остаётся как был — его эта правка не
    // касается.
    expect(
      screen.getByText("Последняя зафиксированная проверка: 01.09.2026 10:00 — Успешно"),
    ).toBeInTheDocument();
  });
});

describe("422 validation errors (T-M2-28)", () => {
  it("save: errors under their fields, the rest above the button", async () => {
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "system_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/admin/ldap-settings") {
        return { status: 200, body: ldapSettingsResponse({ last_tested_at: "2026-09-01T10:00:00", last_test_status: "success" }) };
      }
      if (method === "PUT" && pathname === "/api/admin/ldap-settings") {
        return {
          status: 422,
          body: {
            detail: [
              { loc: ["body", "server_url"], msg: "Значение должно быть строкой.", type: "string_type" },
              { loc: ["body", "port"], msg: "Значение должно быть целым числом.", type: "int_parsing" },
              { loc: ["body"], msg: "Тело запроса должно быть объектом.", type: "model_attributes_type" },
            ],
          },
        };
      }
      return null;
    });

    renderApp("/admin/ldap-settings");
    await screen.findByLabelText("Адрес сервера");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Сохранить/ }));

    expect(screen.getByLabelText("Адрес сервера").closest(".ant-form-item")).toContainElement(
      await screen.findByText("Значение должно быть строкой."),
    );
    expect(screen.getByLabelText("Порт").closest(".ant-form-item")).toContainElement(
      screen.getByText("Значение должно быть целым числом."),
    );
    expect(screen.getByText("Тело запроса должно быть объектом.").closest(".ant-form-item")).toBeNull();
  });

  it("connection test: a field error goes under its field, not into a banner", async () => {
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "system_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/admin/ldap-settings") {
        return { status: 200, body: ldapSettingsResponse({ last_tested_at: "2026-09-01T10:00:00", last_test_status: "success" }) };
      }
      if (method === "POST" && pathname === "/api/admin/ldap-settings/test") {
        return {
          status: 422,
          body: { detail: [{ loc: ["body", "port"], msg: "Значение должно быть целым числом.", type: "int_parsing" }] },
        };
      }
      return null;
    });

    renderApp("/admin/ldap-settings");
    await screen.findByLabelText("Адрес сервера");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Тестовое подключение" }));

    const message = await screen.findByText("Значение должно быть целым числом.");
    expect(screen.getByLabelText("Порт").closest(".ant-form-item")).toContainElement(message);
    expect(screen.queryByText(/port:/)).toBeNull();
  });

  it("connection test: what fits no field stays a general text", async () => {
    setAccessToken("token");
    setFetchHandler((method, pathname) => {
      if (method === "GET" && pathname === "/api/auth/me") {
        return { status: 200, body: meResponse({ role: "system_admin", must_change_password: false }) };
      }
      if (method === "GET" && pathname === "/api/admin/ldap-settings") {
        return { status: 200, body: ldapSettingsResponse({ last_tested_at: "2026-09-01T10:00:00", last_test_status: "success" }) };
      }
      if (method === "POST" && pathname === "/api/admin/ldap-settings/test") {
        return {
          status: 422,
          body: { detail: [{ loc: ["body"], msg: "Тело запроса должно быть объектом.", type: "model_attributes_type" }] },
        };
      }
      return null;
    });

    renderApp("/admin/ldap-settings");
    await screen.findByLabelText("Адрес сервера");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Тестовое подключение" }));

    const message = await screen.findByText("Тело запроса должно быть объектом.");
    expect(message.closest(".ant-form-item")).toBeNull();
  });
});
