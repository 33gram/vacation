import { describe, expect, it } from "vitest";

import { formatDateTime } from "./format";

describe("formatDateTime", () => {
  it("formats an ISO datetime as ДД.ММ.ГГГГ ЧЧ:ММ", () => {
    expect(formatDateTime("2026-10-01T09:05:00")).toBe("01.10.2026 09:05");
  });

  it("ignores seconds, microseconds and a timezone suffix", () => {
    expect(formatDateTime("2026-10-01T09:05:30.123456+00:00")).toBe("01.10.2026 09:05");
    expect(formatDateTime("2026-10-01T09:05:30Z")).toBe("01.10.2026 09:05");
  });

  it("returns an em dash for null", () => {
    expect(formatDateTime(null)).toBe("—");
  });

  it("falls back to the date alone if there's no time part", () => {
    expect(formatDateTime("2026-10-01")).toBe("01.10.2026");
  });
});
