import { describe, expect, it } from "vitest";

import {
  buildYearGrid,
  daysInYear,
  fillDefaults,
  hasUnsetDays,
  isWeekendDate,
  nextDayType,
  toDaysIn,
} from "./productionCalendar";

describe("daysInYear (T-M1-22)", () => {
  it("returns 365 dates for a non-leap year", () => {
    expect(daysInYear(2026)).toHaveLength(365);
  });

  it("returns 366 dates for a leap year", () => {
    expect(daysInYear(2028)).toHaveLength(366);
  });

  it("starts at January 1 and ends at December 31, in order", () => {
    const dates = daysInYear(2026);
    expect(dates[0]).toBe("2026-01-01");
    expect(dates[dates.length - 1]).toBe("2026-12-31");
  });

  it("has no duplicate dates", () => {
    const dates = daysInYear(2026);
    expect(new Set(dates).size).toBe(dates.length);
  });
});

describe("isWeekendDate (T-M1-22)", () => {
  it("treats Saturday and Sunday as weekend", () => {
    // 2026-01-03 — суббота, 2026-01-04 — воскресенье (проверено календарём).
    expect(isWeekendDate("2026-01-03")).toBe(true);
    expect(isWeekendDate("2026-01-04")).toBe(true);
  });

  it("treats a weekday as not weekend", () => {
    // 2026-01-05 — понедельник.
    expect(isWeekendDate("2026-01-05")).toBe(false);
  });
});

describe("buildYearGrid (T-M1-22)", () => {
  it("marks every day absent from the server response as null (unset), not dropped", () => {
    const grid = buildYearGrid(2026, [{ date: "2026-01-01", day_type: "holiday" }]);

    expect(grid).toHaveLength(365);
    expect(grid.find((d) => d.date === "2026-01-01")?.dayType).toBe("holiday");
    expect(grid.find((d) => d.date === "2026-01-02")?.dayType).toBeNull();
  });

  it("returns an all-null grid for an empty server response", () => {
    const grid = buildYearGrid(2026, []);

    expect(grid).toHaveLength(365);
    expect(grid.every((d) => d.dayType === null)).toBe(true);
  });

  it("carries an unexpected day_type value through as-is, not as null or a crash", () => {
    const grid = buildYearGrid(2026, [{ date: "2026-01-01", day_type: "some-future-value" }]);

    expect(grid.find((d) => d.date === "2026-01-01")?.dayType).toBe("some-future-value");
  });
});

describe("nextDayType (T-M1-22)", () => {
  it("cycles working -> weekend -> holiday -> working", () => {
    expect(nextDayType("working")).toBe("weekend");
    expect(nextDayType("weekend")).toBe("holiday");
    expect(nextDayType("holiday")).toBe("working");
  });

  it("resolves an unset (null) day to working on first click", () => {
    expect(nextDayType(null)).toBe("working");
  });

  it("resolves an unknown value to working on first click, same as unset", () => {
    expect(nextDayType("some-future-value")).toBe("working");
  });
});

describe("fillDefaults (T-M1-22)", () => {
  it("fills every unset day: weekend on Saturday/Sunday, working otherwise", () => {
    const grid = buildYearGrid(2026, []);
    const filled = fillDefaults(grid);

    expect(hasUnsetDays(filled)).toBe(false);
    expect(filled.find((d) => d.date === "2026-01-03")?.dayType).toBe("weekend"); // суббота
    expect(filled.find((d) => d.date === "2026-01-05")?.dayType).toBe("working"); // понедельник
  });

  it("does not touch a day that already has a value — safe on a non-empty or partial year", () => {
    const grid = buildYearGrid(2026, [{ date: "2026-01-03", day_type: "holiday" }]);
    const filled = fillDefaults(grid);

    // 2026-01-03 - суббота, "по умолчанию" сделал бы её weekend, но день
    // уже задан (holiday, например, перенос по постановлению) — значение
    // обязано остаться нетронутым.
    expect(filled.find((d) => d.date === "2026-01-03")?.dayType).toBe("holiday");
  });

  it("is a no-op on a fully set year", () => {
    const grid = fillDefaults(buildYearGrid(2026, []));
    const filledAgain = fillDefaults(grid);

    expect(filledAgain).toEqual(grid);
  });
});

describe("hasUnsetDays (T-M1-22)", () => {
  it("is true when at least one day is null", () => {
    expect(hasUnsetDays(buildYearGrid(2026, []))).toBe(true);
  });

  it("is false once every day has a value", () => {
    expect(hasUnsetDays(fillDefaults(buildYearGrid(2026, [])))).toBe(false);
  });
});

describe("toDaysIn (T-M1-22)", () => {
  it("maps every day to {date, day_type} for a fully set year", () => {
    const days = fillDefaults(buildYearGrid(2026, []));
    const body = toDaysIn(days);

    expect(body).toHaveLength(365);
    expect(body[0]).toEqual({ date: "2026-01-01", day_type: "working" });
  });

  it("throws rather than inventing a value for an unset day", () => {
    const days = buildYearGrid(2026, []);
    expect(() => toDaysIn(days)).toThrow();
  });
});
