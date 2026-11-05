import { formValidation, prefixedMessages, stringDetail } from "../api/errors";
import type { LeaveTypeFormSubmitError, LeaveTypeFormValues } from "./LeaveTypeForm";

// Имена полей формы вида отсутствия (совпадают с полями тела запроса); `satisfies` ловит опечатку.
const LEAVE_TYPE_FORM_FIELDS = [
  "code",
  "name",
  "is_paid",
  "unit",
  "reduces_main_balance",
  "balance_tracked",
  "requires_document",
  "requires_approval",
  "annual_limit",
  "carry_over_policy",
  "counted_non_working_days",
  "is_active",
] as const satisfies readonly (keyof LeaveTypeFormValues)[];

/** Отказ сервера по `POST`/`PUT /api/leave-types` -> форма. Текст сервера показывается как есть
 * (`ui.md` §7), без префикса поля. Поле определяется так:
 *   - `400` (`T-M2-27`): текст несёт префикс(ы) `<поле>: ` (`code:` - смена кода; `is_active:` и/или
 *     `balance_tracked:` - защита основного вида, оба через `"; "`, когда нарушены оба) - каждый
 *     текст под своим полем. Поле, которого в форме нет, и текст без префикса - общим текстом над
 *     кнопками. Из ЭТОЙ формы достижим только отказ основного вида: поле кода в правке отключено, и
 *     `code:` закрывают тест разбора и backend-тест, а не тест формы;
 *   - `POST` 409 - один отказ, дубль кода -> под полем «Код» (префикса у него нет, поле по паре
 *     «метод - статус»);
 *   - `PUT` 409 - один отказ, у вида есть события журнала, а признак «по виду ведётся остаток»
 *     снимают -> под полем «По виду ведётся остаток» (тоже по паре «метод - статус»). */
export function leaveTypeSubmitError(
  method: "POST" | "PUT",
  status: number,
  detail: unknown,
  fallbackMessage: string,
): LeaveTypeFormSubmitError {
  // 422 (`T-M2-28`): ВСЕ ошибки тела - под своими полями (в том числе шаблон кода, который
  // проверяет сервер), остальное - общим текстом над кнопками.
  const validation = status === 422 ? formValidation(detail, LEAVE_TYPE_FORM_FIELDS) : null;
  if (validation) {
    return {
      fieldErrors: validation.fieldErrors,
      generalError: validation.generalError,
    };
  }
  const prefixed = status === 400 ? prefixedMessages(detail, LEAVE_TYPE_FORM_FIELDS) : null;
  if (prefixed) {
    return {
      fieldErrors: prefixed.map(({ field, message }) => ({
        field: field as (typeof LEAVE_TYPE_FORM_FIELDS)[number],
        message,
      })),
      generalError: null,
    };
  }
  const message = stringDetail(detail) ?? fallbackMessage;
  if (method === "POST" && status === 409) {
    return { fieldErrors: [{ field: "code", message }], generalError: null };
  }
  if (method === "PUT" && status === 409) {
    return { fieldErrors: [{ field: "balance_tracked", message }], generalError: null };
  }
  return { fieldErrors: [], generalError: message };
}
