/** Текст ошибки, пришедший в `detail` как строка. FastAPI-обработчики
 * 401/403/409 в `main.py` (`AuthenticationError`, `AccessDeniedError`,
 * `WeakPasswordError`'s текстовые ветки, `IntegrityError`) регистрируются
 * ГЛОБАЛЬНО (`@app.exception_handler(...)`), а не через `responses={...}`
 * на конкретном маршруте — поэтому в OpenAPI-схему операции они не
 * попадают, и сгенерированный клиент не знает их формы для конкретного
 * эндпоинта (например, `POST /api/auth/login` в схеме объявляет только
 * 200/422). Тело ответа при этом всё равно приходит именно в этой форме —
 * этот помощник читает его защитно, а не полагается на (неполный для
 * такого случая) сгенерированный тип. `ui.md` §7: текст показывается как
 * есть, не переписывается на клиенте. */
export function stringDetail(detail: unknown): string | null {
  return typeof detail === "string" ? detail : null;
}

/** `POST /api/auth/password/change` заявляет в схеме и 400 (`ErrorDetail`:
 * `detail` — строка ИЛИ список `PasswordPolicyViolationOut`), и 422
 * (`HTTPValidationError`: `detail` — список `ValidationError`) — оба
 * массива синтаксически, но с разными полями. `Array.isArray` одной
 * проверкой их не различает; здесь сверяется форма элемента (`code` +
 * `message`), которая есть только у нарушений парольной политики. */
export function violationList(detail: unknown): { code: string; message: string }[] | null {
  if (
    Array.isArray(detail) &&
    detail.every(
      (item): item is { code: string; message: string } =>
        typeof item === "object" &&
        item !== null &&
        typeof (item as Record<string, unknown>).code === "string" &&
        typeof (item as Record<string, unknown>).message === "string",
    )
  ) {
    return detail;
  }
  return null;
}
