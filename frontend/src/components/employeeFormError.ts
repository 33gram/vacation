import { fieldError, formValidation, stringDetail, violationList } from "../api/errors";

// Поля формы сотрудника (имена `Form.Item` в `EmployeeForm`); поле «Пароль» в теле запроса называется
// `initial_password` (создание) и `new_password` (правка) - `aliases` ниже.
const EMPLOYEE_FORM_FIELDS = [
  "full_name",
  "personnel_number",
  "department_id",
  "position",
  "hire_date",
  "manager_id",
  "email",
  "work_schedule_type",
  "leave_category",
  "auth_type",
  "password",
] as const;
const PASSWORD_ALIASES = { initial_password: "password", new_password: "password" } as const;

export interface EmployeeFormSubmitError {
  fieldErrors: { field: string; message: string }[];
  violatedCodes: Set<string> | null;
  generalError: string | null;
}

/** Разбор `error.detail` из `POST /api/employees`/`PUT /api/employees/
 * {id}` в форму, которую понимает `EmployeeForm` — было продублировано
 * дословно в `EmployeeCreatePage` и `EmployeeCardPage` (PR #153 review,
 * п. 3). Ровно три формы ответа 400 (`api/errors.ts`): префикс поля
 * (`email: …`), список нарушений парольной политики
 * (`PasswordPolicyViolationOut[]`) — обе узнаются по форме `detail`, не
 * по эндпоинту, — и всё остальное показывается как есть (`ui.md` §7).
 * `fallbackMessage` — единственное, что осталось разным между create и
 * edit ("Не удалось создать/сохранить карточку."). В отдельном от
 * `EmployeeForm.tsx` файле — рядом с ним `react-refresh/only-export-
 * components` предупреждает на экспорт функции из файла компонента (тот
 * же случай, что и `SessionContext`/`useSession`, PR #144 review). */
export function parseEmployeeFormError(
  detail: unknown,
  fallbackMessage: string,
): EmployeeFormSubmitError {
  // 422 (`T-M2-28`): ВСЕ ошибки тела - под своими полями, остальное - общим текстом над кнопками.
  const validation = formValidation(detail, EMPLOYEE_FORM_FIELDS, PASSWORD_ALIASES);
  if (validation) {
    return {
      fieldErrors: validation.fieldErrors,
      violatedCodes: null,
      generalError: validation.generalError,
    };
  }
  const matchedFieldError = fieldError(detail);
  const violations = violationList(detail);
  return {
    fieldErrors: matchedFieldError ? [matchedFieldError] : [],
    violatedCodes: violations ? new Set(violations.map((violation) => violation.code)) : null,
    generalError: !matchedFieldError && !violations ? (stringDetail(detail) ?? fallbackMessage) : null,
  };
}
