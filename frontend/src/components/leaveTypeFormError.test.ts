import { describe, expect, it } from "vitest";
import { leaveTypeSubmitError } from "./leaveTypeFormError";

const FALLBACK = "Не удалось сохранить вид.";

describe("leaveTypeSubmitError: PUT 400 prefixes (T-M2-27)", () => {
  // Из формы вида отсутствий `code:` недостижим (поле кода в правке отключено): отказ закрыт
  // этим разбором и backend-тестом `test_changing_the_code_is_refused_not_ignored`.
  it("`code:` goes under the code field", () => {
    expect(leaveTypeSubmitError("PUT", 400, "code: Код не меняется", FALLBACK)).toEqual({
      fieldErrors: [{ field: "code", message: "Код не меняется" }],
      generalError: null,
    });
  });

  it("both main type fields: two messages, in the order of the text", () => {
    const result = leaveTypeSubmitError("PUT", 400, "is_active: а; balance_tracked: б", FALLBACK);

    expect(result).toEqual({
      fieldErrors: [
        { field: "is_active", message: "а" },
        { field: "balance_tracked", message: "б" },
      ],
      generalError: null,
    });
  });

  it.each([
    ["no prefix", "Код менять нельзя"],
    ["a field the form does not have", "full_name: плохо"],
  ])("%s: the whole text above the buttons", (_name, detail) => {
    expect(leaveTypeSubmitError("PUT", 400, detail, FALLBACK)).toEqual({
      fieldErrors: [],
      generalError: detail,
    });
  });

  it("a non-string detail falls back to the general message", () => {
    expect(leaveTypeSubmitError("PUT", 400, undefined, FALLBACK)).toEqual({
      fieldErrors: [],
      generalError: FALLBACK,
    });
  });
});
