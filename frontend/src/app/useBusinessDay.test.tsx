import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { meResponse } from "../test-utils/fixtures";
import { SessionProvider } from "./SessionContext";
import { useBusinessDay } from "./useBusinessDay";

function dayFor(today: unknown): string | null {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <SessionProvider value={{ ...meResponse(), today } as ReturnType<typeof meResponse>}>
      {children}
    </SessionProvider>
  );
  return renderHook(() => useBusinessDay(), { wrapper }).result.current;
}

describe("useBusinessDay accepts only a real calendar date (PR #197 review, Д-3)", () => {
  it.each(["2026-02-30", "0000-00-00", "2026-13-45", "2025-02-29", "2026-00-10", "2026-04-31"])(
    "%s is not a date: the day is unknown",
    (value) => {
      expect(dayFor(value)).toBeNull();
    },
  );

  it.each(["2026-02-28", "2024-02-29", "2026-12-31", "2000-02-29", "2026-01-01"])(
    "%s is a date: it is the day",
    (value) => {
      expect(dayFor(value)).toBe(value);
    },
  );

  it.each([undefined, null, 20261231, "", "2026-1-1", "26-12-31", " 2026-12-31", "2026-12-31T00:00"])(
    "%j is not the YYYY-MM-DD form: the day is unknown",
    (value) => {
      expect(dayFor(value)).toBeNull();
    },
  );
});
