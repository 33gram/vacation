import { describe, expect, it } from "vitest";

import {
  type EntryType,
  type Unit,
  entryTypeLabel,
  formatAmount,
  formatQuantity,
  unitLabel,
} from "./balance";

describe("balance display helpers (FR-BAL-002)", () => {
  it("formats the server's decimal string without any arithmetic", () => {
    expect(formatQuantity("18.67")).toBe("18,67");
    expect(formatQuantity("28.00")).toBe("28");
    expect(formatQuantity("-3.50")).toBe("-3,5");
    expect(formatQuantity("0.00")).toBe("0");
    expect(formatQuantity("-0.00")).toBe("0");
    expect(formatQuantity("12")).toBe("12");
  });

  it("puts an explicit plus on positive quantities only when asked", () => {
    expect(formatQuantity("2.33", { signed: true })).toBe("+2,33");
    expect(formatQuantity("-2.33", { signed: true })).toBe("-2,33");
    expect(formatQuantity("0.00", { signed: true })).toBe("0");
    expect(formatQuantity("2.33")).toBe("2,33");
  });

  it("takes the unit label from one place and shows an unknown unit as it came", () => {
    expect(unitLabel("working_days")).toBe("раб. дн.");
    expect(unitLabel("calendar_days")).toBe("кал. дн.");
    expect(unitLabel("hours")).toBe("ч");
    // Значение вне союза (рассинхрон версий бэкенда и фронтенда) в тип не вписывается: `as` нужен
    // ровно здесь, чтобы смоделировать ответ в обход схемы.
    expect(unitLabel("weeks" as Unit)).toBe("weeks");
    expect(formatAmount("8.00", "hours")).toBe("8 ч");
  });

  it("shows an unknown entry type as it came", () => {
    expect(entryTypeLabel("accrual")).toBe("Начисление");
    // Значение вне союза (рассинхрон версий бэкенда и фронтенда) в тип не вписывается: `as` нужен
    // ровно здесь, чтобы смоделировать ответ в обход схемы.
    expect(entryTypeLabel("something_new" as EntryType)).toBe("something_new");
  });
});
