import { fieldError, stringDetail, violationList } from "../api/errors";

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
  const matchedFieldError = fieldError(detail);
  const violations = violationList(detail);
  return {
    fieldErrors: matchedFieldError ? [matchedFieldError] : [],
    violatedCodes: violations ? new Set(violations.map((violation) => violation.code)) : null,
    generalError: !matchedFieldError && !violations ? (stringDetail(detail) ?? fallbackMessage) : null,
  };
}
