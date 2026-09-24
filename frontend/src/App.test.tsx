import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  // T-M0-10 (заглушка авторизации) больше нет — `AuthGuard` теперь
  // проверяет реальное наличие токена (`hasStoredAccessToken`), а не
  // фиктивный. Отрицательный контроль: без `AuthGuard` (или с ним,
  // всегда пропускающим дальше) этот тест обнаружил бы `AppShell`/`GET
  // /api/auth/me`, а не форму входа.
  it("redirects an unauthenticated visitor to the login page", () => {
    render(<App />);

    expect(screen.getByText("Система учёта отпусков")).toBeInTheDocument();
    expect(screen.getByLabelText("Логин или e-mail")).toBeInTheDocument();
  });
});
