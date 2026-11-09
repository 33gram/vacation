import { describe, expect, it } from "vitest";
import {
  detailText,
  FIELD_ERROR_PREFIXES,
  fieldError,
  formValidation,
  layoutValidation,
  prefixedMessages,
  validationIssues,
  violationList,
  withoutFieldPrefixes,
} from "./errors";

const BODY_FIELD = { loc: ["body", "code"], msg: "Значение не соответствует допустимому формату.", type: "string_pattern_mismatch" };
const BODY_FIELD_2 = { loc: ["body", "code"], msg: "Слишком длинное значение: не более 64 символов.", type: "string_too_long" };
const OTHER_FIELD = { loc: ["body", "name"], msg: "Обязательное поле.", type: "missing" };
const WHOLE_BODY = { loc: ["body"], msg: "Тело запроса должно быть объектом.", type: "model_attributes_type" };
const PATH = { loc: ["path", "employee_id"], msg: "Значение должно быть целым числом.", type: "int_parsing" };
const NESTED = { loc: ["body", "values", "annual_leave_days"], msg: "Значение должно быть строкой.", type: "string_type" };
const POLICY = [
  { code: "too_short", message: "Не менее 12 символов." },
  { code: "no_digit", message: "Нужна цифра." },
];

describe("validationIssues", () => {
  it("reads the {loc, msg, type} list and takes the LAST loc element as the field", () => {
    expect(validationIssues([BODY_FIELD, NESTED])).toEqual([
      { field: "code", name: "code", message: BODY_FIELD.msg },
      { field: "annual_leave_days", name: "annual_leave_days", message: NESTED.msg },
    ]);
  });

  it("gives no field for an error on the whole body and for path/query errors", () => {
    expect(validationIssues([WHOLE_BODY, PATH])).toEqual([
      { field: null, name: null, message: WHOLE_BODY.msg },
      { field: null, name: "employee_id", message: PATH.msg },
    ]);
  });

  it("does not mix up the two list shapes: the password-policy list is not validation errors", () => {
    expect(validationIssues(POLICY)).toBeNull();
    expect(violationList(POLICY)).toEqual(POLICY);
    expect(violationList([BODY_FIELD])).toBeNull();
    expect(validationIssues([BODY_FIELD])).not.toBeNull();
  });

  it("is null for a string, an empty list and a list with a foreign element", () => {
    expect(validationIssues("email: занят")).toBeNull();
    expect(validationIssues([])).toBeNull();
    expect(validationIssues([BODY_FIELD, { code: "x", message: "y" }])).toBeNull();
    expect(validationIssues(undefined)).toBeNull();
  });
});

describe("layoutValidation", () => {
  const known = ["code", "name"];

  it("shows ALL violations at once, several of one field in one text", () => {
    const layout = layoutValidation(validationIssues([BODY_FIELD, BODY_FIELD_2, OTHER_FIELD])!, known);

    expect(layout.fieldErrors).toEqual([
      { field: "code", message: `${BODY_FIELD.msg} ${BODY_FIELD_2.msg}` },
      { field: "name", message: OTHER_FIELD.msg },
    ]);
    expect(layout.generalError).toBeNull();
  });

  it("puts the whole-body error and the path error into the general text, not under a field", () => {
    const layout = layoutValidation(validationIssues([WHOLE_BODY, PATH])!, known);

    expect(layout.fieldErrors).toEqual([]);
    expect(layout.generalError).toBe(`${WHOLE_BODY.msg} employee_id: ${PATH.msg}`);
  });

  it("does not lay an error of a field the form does not have under a random field", () => {
    const layout = layoutValidation(validationIssues([NESTED, OTHER_FIELD])!, known);

    expect(layout.fieldErrors).toEqual([{ field: "name", message: OTHER_FIELD.msg }]);
    expect(layout.generalError).toBe(`annual_leave_days: ${NESTED.msg}`);
  });

  it("maps a body field to a differently named form field through aliases", () => {
    const issue = { loc: ["body", "initial_password"], msg: "Обязательное поле.", type: "missing" };
    const layout = layoutValidation(validationIssues([issue])!, ["password"], { initial_password: "password" });

    expect(layout.fieldErrors).toEqual([{ field: "password", message: "Обязательное поле." }]);
  });
});

describe("formValidation and detailText", () => {
  it("formValidation is null when detail is not a validation list", () => {
    expect(formValidation("email: занят", ["email"])).toBeNull();
    expect(formValidation(POLICY, ["email"])).toBeNull();
  });

  it("detailText returns the string as is, joins validation errors, and is null for anything else", () => {
    expect(detailText("Доступ запрещён.")).toBe("Доступ запрещён.");
    expect(detailText([OTHER_FIELD, WHOLE_BODY])).toBe(`name: ${OTHER_FIELD.msg} ${WHOLE_BODY.msg}`);
    expect(detailText(POLICY)).toBeNull();
    expect(detailText(undefined)).toBeNull();
  });
});

describe("prefixedMessages and fieldError (T-M2-27)", () => {
  const FIELDS = ["is_active", "balance_tracked", "code"];

  it("returns every prefix in order of appearance", () => {
    expect(prefixedMessages("is_active: первый; balance_tracked: второй", FIELDS)).toEqual([
      { field: "is_active", message: "первый" },
      { field: "balance_tracked", message: "второй" },
    ]);
  });

  it("returns one entry for one prefix (the single case is the plural with one element)", () => {
    expect(prefixedMessages("code: Код не меняется", FIELDS)).toEqual([
      { field: "code", message: "Код не меняется" },
    ]);
  });

  it.each([
    ["plain text", "Пароль слишком короткий"],
    ["a word that is not a known field", "unknown_key: плохо"],
    ["text before the first prefix", "ошибка: code: плохо"],
    ["not a string", ["code: плохо"]],
  ])("returns null for %s", (_name, detail) => {
    expect(prefixedMessages(detail, FIELDS)).toBeNull();
  });

  it("returns null when no field is known", () => {
    expect(prefixedMessages("code: плохо", [])).toBeNull();
  });

  it("keeps a `; ` that is not followed by a known prefix inside the text", () => {
    expect(prefixedMessages("code: а; б: в", FIELDS)).toEqual([{ field: "code", message: "а; б: в" }]);
  });

  it("fieldError: only the fields of the calling form are its prefixes", () => {
    expect(fieldError("code: Код не меняется", FIELDS)).toEqual({ field: "code", message: "Код не меняется" });
    expect(fieldError("is_active: нельзя", FIELDS)).toEqual({ field: "is_active", message: "нельзя" });
    // Префикс чужого поля - не префикс этой формы: общий текст, а не поле, которого нет.
    expect(fieldError("code: Код не меняется", ["email", "manager_id"])).toBeNull();
  });

  it("FIELD_ERROR_PREFIXES is the guard's list, not a form's: it holds every backend prefix", () => {
    expect(FIELD_ERROR_PREFIXES).toEqual(expect.arrayContaining(["email", "head_employee_id", "code", "is_active"]));
  });

  it("withoutFieldPrefixes strips every known prefix and joins the texts like layoutValidation does", () => {
    expect(withoutFieldPrefixes("code: Код не меняется")).toBe("Код не меняется");
    expect(withoutFieldPrefixes("is_active: нельзя; balance_tracked: тоже нельзя")).toBe("нельзя тоже нельзя");
    expect(withoutFieldPrefixes("Пароль слишком короткий")).toBe("Пароль слишком короткий");
    expect(withoutFieldPrefixes("unknown_key: плохо")).toBe("unknown_key: плохо");
    expect(withoutFieldPrefixes(["code: x"])).toBeNull();
  });

  it("detailText returns a string detail without its field prefix", () => {
    expect(detailText("head_employee_id: уволенный сотрудник")).toBe("уволенный сотрудник");
  });

  it("fieldError: a message that starts with an ordinary word is not a field error", () => {
    expect(fieldError("Пароль: не подходит", FIELDS)).toBeNull();
    expect(fieldError("weak: не подходит", FIELDS)).toBeNull();
    expect(fieldError(["code: x"], FIELDS)).toBeNull();
  });

  it("fieldError: the message is the whole rest of the string after the first prefix", () => {
    expect(fieldError("email: адрес занят; manager_id: сотрудник не найден", ["email", "manager_id"])).toEqual({
      field: "email",
      message: "адрес занят; manager_id: сотрудник не найден",
    });
  });
});
