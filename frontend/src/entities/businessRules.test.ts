import { describe, expect, it } from "vitest";

import { businessRuleResponse } from "../test-utils/fixtures";
import { changedPairs, checkBeforeSend, rulesErrorsByKey } from "./businessRules";

const KEYS = ["annual_leave_days", "schedule_approval_deadline", "password_min_length"];

describe("rulesErrorsByKey", () => {
  it("splits `key: text; key: text` into one message per key", () => {
    expect(
      rulesErrorsByKey("annual_leave_days: плохо; password_min_length: тоже плохо", KEYS),
    ).toEqual({ annual_leave_days: "плохо", password_min_length: "тоже плохо" });
  });

  it("keeps a message that itself contains a semicolon and parentheses", () => {
    expect(
      rulesErrorsByKey("schedule_approval_deadline: нужна дата (например, 12-17); без 29 февраля", KEYS),
    ).toEqual({ schedule_approval_deadline: "нужна дата (например, 12-17); без 29 февраля" });
  });

  it.each([
    ["plain text without a key", "что-то пошло не так"],
    ["a key the form does not know", "unknown_key: плохо"],
    ["text before the first key", "ошибка: annual_leave_days: плохо"],
    ["not a string", ["annual_leave_days: плохо"]],
  ])("returns null for %s (shown whole as a general error)", (_name, detail) => {
    expect(rulesErrorsByKey(detail, KEYS)).toBeNull();
  });
});

describe("changedPairs and checkBeforeSend", () => {
  const rules = [
    businessRuleResponse({ key: "annual_leave_days", value: "28" }),
    businessRuleResponse({ key: "password_min_length", value: "8", minimum: 8, maximum: 72 }),
  ];

  it("keeps only the pairs that differ from the saved value", () => {
    expect(changedPairs(rules, { annual_leave_days: "28", password_min_length: "9" })).toEqual({
      password_min_length: "9",
    });
  });

  it("treats '028' as changed: the server decides that it equals 28", () => {
    expect(changedPairs(rules, { annual_leave_days: "028", password_min_length: "8" })).toEqual({
      annual_leave_days: "028",
    });
  });

  it("checks an integer against the range of the response, and only an integer", () => {
    expect(checkBeforeSend(rules[1], "7")).toBe("От 8 до 72.");
    expect(checkBeforeSend(rules[1], "73")).toBe("От 8 до 72.");
    expect(checkBeforeSend(rules[1], "8")).toBeNull();
    expect(checkBeforeSend(rules[1], "")).toBe("Нужно целое число. От 8 до 72.");
    expect(checkBeforeSend(rules[1], "1e1")).toBe("Нужно целое число. От 8 до 72.");
    expect(
      checkBeforeSend(businessRuleResponse({ value_type: "month_day", minimum: null, maximum: null }), "99-99"),
    ).toBeNull();
  });
});
