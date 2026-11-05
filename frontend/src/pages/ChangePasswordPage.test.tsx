import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { setAccessToken } from "../app/auth";
import { setFetchHandler } from "../test-utils/fetchMock";
import { meResponse, PASSWORD_POLICY_HINTS } from "../test-utils/fixtures";
import { renderApp } from "../test-utils/renderApp";

function mockBackend(changeReply: { status: number; body: unknown }) {
  setAccessToken("token");
  setFetchHandler((method, pathname) => {
    if (method === "GET" && pathname === "/api/auth/me") {
      return { status: 200, body: meResponse({ role: "hr_admin", must_change_password: true }) };
    }
    if (method === "GET" && pathname === "/api/auth/password-policy") {
      return { status: 200, body: PASSWORD_POLICY_HINTS };
    }
    if (method === "POST" && pathname === "/api/auth/password/change") {
      return changeReply;
    }
    return null;
  });
}

async function submit(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText("Текущий пароль"), "Old1!pass");
  await user.type(screen.getByLabelText("Новый пароль"), "New1!passwd");
  await user.click(screen.getByRole("button", { name: "Сменить пароль" }));
}

describe("password change: two different lists in detail (T-M2-28)", () => {
  it("a 422 {loc,msg,type} list goes under the password fields and is NOT taken for policy violations", async () => {
    mockBackend({
      status: 422,
      body: {
        detail: [
          { loc: ["body", "current_password"], msg: "Слишком длинное значение: не более 128 символов.", type: "string_too_long" },
          { loc: ["body", "new_password"], msg: "Обязательное поле.", type: "missing" },
        ],
      },
    });

    renderApp("/");
    await submit(userEvent.setup());

    const under = (label: string) => screen.getByLabelText(label).closest(".ant-form-item");
    expect(under("Текущий пароль")).toContainElement(
      await screen.findByText("Слишком длинное значение: не более 128 символов."),
    );
    expect(under("Новый пароль")).toContainElement(screen.getByText("Обязательное поле."));
    // Ни один пункт чек-листа не подсвечен как нарушенный (`code`/`message` в списке не было).
    expect(screen.queryByText("Не удалось изменить пароль.")).not.toBeInTheDocument();
  });

  it("a 400 {code,message} list still marks the violated rules and puts nothing under the fields", async () => {
    mockBackend({
      status: 400,
      body: { detail: [{ code: "too_short", message: "Не менее 12 символов." }] },
    });

    renderApp("/");
    await submit(userEvent.setup());

    await screen.findByText("Требования к паролю");
    expect(document.querySelectorAll(".ant-form-item-explain-error")).toHaveLength(0);
  });
});
