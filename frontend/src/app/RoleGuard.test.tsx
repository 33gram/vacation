import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { meResponse } from "../test-utils/fixtures";
import { RoleGuard } from "./RoleGuard";
import { SessionProvider } from "./SessionContext";

describe("RoleGuard", () => {
  it("denies a path outside MENU_ITEMS, even for a role with access to every other M1 section", () => {
    // `system_admin` видит все четыре сегодняшних раздела (`ui.md` §2.2) —
    // отказ здесь показывает, что дело именно в ОТСУТСТВИИ пути в
    // `MENU_ITEMS` (T-M1-19 добавит `/employees/{id}`, которого там нет и
    // не будет), а не в нехватке прав у роли на известный раздел.
    render(
      <MemoryRouter>
        <SessionProvider value={meResponse({ role: "system_admin" })}>
          <RoleGuard path="/employees/42">
            <div>Секретное содержимое</div>
          </RoleGuard>
        </SessionProvider>
      </MemoryRouter>,
    );

    expect(screen.queryByText("Секретное содержимое")).not.toBeInTheDocument();
    expect(
      screen.getByText("Доступ запрещён. Обратитесь к HR-администратору."),
    ).toBeInTheDocument();
  });
});
