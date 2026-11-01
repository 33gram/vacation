import { stringDetail } from "../api/errors";
import type { LeaveTypeFormSubmitError } from "./LeaveTypeForm";

/** Отказ сервера по `POST`/`PUT /api/leave-types` -> форма. Текст сервера показывается как есть
 * (`ui.md` §7). Машиночитаемого префикса поля у этих отказов НЕТ (в отличие от
 * `personnel_number:`/`email:`), поэтому поле определяется по паре «метод - статус»:
 *   - `POST` 409 - один отказ, дубль кода -> под полем «Код»;
 *   - `PUT` 409 - один отказ, у вида есть события журнала, а признак «по виду ведётся остаток»
 *     снимают -> под полем «По виду ведётся остаток»;
 *   - `PUT` 400 - ДВА разных отказа (смена кода; деактивация или снятие признака у ОСНОВНОГО вида)
 *     с одним кодом и без признака поля -> общим текстом над кнопками. Сменить код из этой формы
 *     нельзя (поле отключено), так что на практике приходит второй.
 * Различать два `PUT` 400 по тексту нельзя; если понадобится поле - бэкенду нужен префикс, это его
 * правка, не этого файла. */
export function leaveTypeSubmitError(
  method: "POST" | "PUT",
  status: number,
  detail: unknown,
  fallbackMessage: string,
): LeaveTypeFormSubmitError {
  const message = stringDetail(detail) ?? fallbackMessage;
  if (method === "POST" && status === 409) {
    return { fieldErrors: [{ field: "code", message }], generalError: null };
  }
  if (method === "PUT" && status === 409) {
    return { fieldErrors: [{ field: "balance_tracked", message }], generalError: null };
  }
  return { fieldErrors: [], generalError: message };
}
