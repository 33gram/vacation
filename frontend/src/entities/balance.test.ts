import { describe, expect, it } from "vitest";

import {
  type EntryType,
  type Unit,
  entryTypeLabel,
  formatAmount,
  formatQuantity,
  historyRows,
  unitLabel,
} from "./balance";
import { accrualYearResponse, balanceEntryResponse, leaveTypeBalanceResponse } from "../test-utils/fixtures";

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

describe("history rows (T-M2-30, ui.md §6)", () => {
  it("puts the newest first and does not change the order of the response it was given", () => {
    const balance = leaveTypeBalanceResponse({
      history: [
        balanceEntryResponse({ id: 1, entry_date: "2026-01-05" }),
        balanceEntryResponse({ id: 2, entry_date: "2026-05-02" }),
      ],
      accrual_years: [accrualYearResponse({ last_accrual_date: "2026-03-01" })],
    });

    expect(historyRows(balance).map((row) => row.entry_date)).toEqual([
      "2026-05-02",
      "2026-03-01",
      "2026-01-05",
    ]);
    expect(balance.history.map((entry) => entry.id)).toEqual([1, 2]); // вход не переставлен
  });

  it("on the same date puts the later written event first and the folded accruals below events", () => {
    const rows = historyRows(
      leaveTypeBalanceResponse({
        history: [
          balanceEntryResponse({ id: 7, entry_date: "2026-10-24" }),
          balanceEntryResponse({ id: 9, entry_date: "2026-10-24" }),
        ],
        accrual_years: [accrualYearResponse({ last_accrual_date: "2026-10-24" })],
      }),
    );

    expect(rows.map((row) => row.key)).toEqual(["event-9", "event-7", "accrual-year-2026-03-10"]);
  });

  it("carries the server's sum of a folded year as it came, without adding anything", () => {
    const rows = historyRows(
      leaveTypeBalanceResponse({
        history: [],
        accrual_years: [
          accrualYearResponse({ working_year_start: "2025-03-10", accrued: "27.96" }),
          accrualYearResponse({ working_year_start: "2026-03-10", accrued: "18.67" }),
        ],
      }),
    );

    expect(rows.map((row) => row.quantity).sort()).toEqual(["18.67", "27.96"]);
    expect(rows.every((row) => row.folded && row.id === null && row.actor_name === null)).toBe(true);
  });
});
