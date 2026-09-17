import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  it("redirects an unauthenticated visitor to the login page", () => {
    render(<App />);

    expect(screen.getByText("Система учёта отпусков")).toBeInTheDocument();
    expect(screen.getByLabelText("Корпоративный e-mail")).toBeInTheDocument();
  });
});
