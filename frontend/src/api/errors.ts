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
// T-M1-19: поля карточки сотрудника, для которых сервис
// (`employee_service.py`/`unique_violations.py`) добавляет к тексту
// машиночитаемый префикс `"<поле>: "` (T-M1-47) — по нему интерфейс
// кладёт ошибку под нужное поле формы, а сам префикс пользователю не
// показывается (`ui.md` §7: место ошибки — под полем). Раздел
// «Подразделения» (`department_service.py`) этого соглашения пока не
// использует — его тексты ("Родительское подразделение … не найдено",
// "Сотрудник … не найден") без префикса поля, проверено чтением
// `department_service.py`; T-M1-20 не должна рассчитывать на раскладку
// ошибок по полям там, где её нет. Список закрыт и совпадает с полями,
// которые сервис размечает так сегодня — новое поле с этим же
// соглашением придётся добавить сюда явно, молчаливого совпадения по
// любому `"слово: "` в начале строки не будет: текст ошибки не обязан
// быть в этой форме (например, `WeakPasswordError` начинается с
// "Пароль", а не с имени поля).
const FIELD_ERROR_PREFIXES = ["manager_id", "email", "personnel_number", "department_id"] as const;

/** Разбирает `stringDetail`-совместимый текст на `{field, message}`, если
 * он начинается с одного из известных префиксов, или возвращает `null`
 * (не наш случай — общая ошибка операции, `ui.md` §7). */
export function fieldError(detail: unknown): { field: string; message: string } | null {
  const text = stringDetail(detail);
  if (text === null) {
    return null;
  }
  for (const field of FIELD_ERROR_PREFIXES) {
    const prefix = `${field}: `;
    if (text.startsWith(prefix)) {
      return { field, message: text.slice(prefix.length) };
    }
  }
  return null;
}

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
