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
// Поля, для которых сервис добавляет к тексту ошибки машиночитаемый
// префикс `"<поле>: "` (T-M1-47) — по нему интерфейс кладёт ошибку под
// нужное поле формы, а сам префикс пользователю не показывается
// (`ui.md` §7: место ошибки — под полем). Проверено разбором
// синтаксического дерева всех `raise` в `app/`, не построчным `grep`
// (T-M1-20 review — построчный `grep` не видит `raise`, у которого
// текст ошибки на следующей строке, и именно так предыдущая версия
// этого комментария ошибочно решила, что раздел «Подразделения» вовсе
// не размечает ошибки по полю): `employee_service.py` —
// `department_id`, `manager_id`; `unique_violations.py` —
// `personnel_number`, `email` (тексты оттуда, `raise ValueError(...)` —
// в вызывающем коде, `employee_service.py`); `department_service.py` —
// `head_employee_id` (строка про уволенного руководителя). Пять полей,
// не четыре. Список закрыт и совпадает с полями, которые сервис
// размечает так сегодня — новое поле с этим же соглашением придётся
// добавить сюда явно, молчаливого совпадения по любому `"слово: "` в
// начале строки не будет: текст ошибки не обязан быть в этой форме
// (например, `WeakPasswordError` начинается с "Пароль", а не с имени
// поля).
const FIELD_ERROR_PREFIXES = [
  "manager_id",
  "email",
  "personnel_number",
  "department_id",
  "head_employee_id",
] as const;

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
