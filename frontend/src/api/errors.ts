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

/** Одна ошибка валидации тела/параметров запроса из `422`: `{loc, msg, type}` (`main.py`,
 * `_handle_request_validation_error`). `msg` приходит УЖЕ русским с сервера (`app/core/
 * validation_texts.py`, `NFR-LOC-001`) и показывается как есть - таблицы текстов на клиенте нет
 * и быть не должно (`ui.md` §7). `field` - имя поля формы: последний элемент `loc` у ошибки в
 * теле (`["body", "<поле>"]`, у вложенных - `["body", "values", "<ключ>"]`); `null` у ошибки на
 * всём теле (`["body"]`) и у ошибок пути/запроса. `name` - последний строковый элемент `loc`
 * для случаев, когда ошибку надо показать общим текстом, а имя поля важно. */
export interface ValidationIssue {
  field: string | null;
  name: string | null;
  message: string;
}

function isValidationItem(
  item: unknown,
): item is { loc: (string | number)[]; msg: string; type: string } {
  if (typeof item !== "object" || item === null) {
    return false;
  }
  const record = item as Record<string, unknown>;
  return (
    Array.isArray(record.loc) &&
    typeof record.msg === "string" &&
    typeof record.type === "string"
  );
}

/** Список ошибок валидации из `detail` или `null`, если это не он. ОТЛИЧАЕТСЯ от
 * `violationList` формой элемента (`loc`/`msg`/`type` против `code`/`message` у нарушений
 * парольной политики): оба ответа - массивы, `Array.isArray` их не различает, поэтому форма
 * элемента проверяется у каждого (причина - в докстринге `violationList`). Пустой массив - не
 * ошибки валидации. Ничего не разбирается из текста: клиентской копии шаблонов и ограничений
 * схемы нет (шаблон кода живёт в схеме бэкенда, `T-M2-28`). */
export function validationIssues(detail: unknown): ValidationIssue[] | null {
  if (!Array.isArray(detail) || detail.length === 0 || !detail.every(isValidationItem)) {
    return null;
  }
  return detail.map((item) => {
    const last = item.loc[item.loc.length - 1];
    const wholeBody = item.loc.length === 1 && item.loc[0] === "body";
    const name = typeof last === "string" && !wholeBody ? last : null;
    const inBody = item.loc[0] === "body" && item.loc.length > 1;
    return { field: inBody ? name : null, name, message: item.msg };
  });
}

export interface ValidationLayout {
  /** По одной записи на поле формы; несколько нарушений одного поля идут одним текстом. */
  fieldErrors: { field: string; message: string }[];
  /** Всё, что не легло под поле: ошибка на всём теле, ошибка пути/запроса и ошибка поля, которого
   * в форме нет. Показывается общим текстом над кнопками (`ui.md` §7). `null` - нечего показывать. */
  generalError: string | null;
}

/** Раскладывает ВСЕ ошибки сразу (`ui.md` §7), не первую. `knownFields` - имена полей ЭТОЙ формы;
 * `aliases` - когда поле формы называется иначе, чем поле тела (`initial_password` -> `password`).
 * Ошибка поля, которого в форме нет, не кладётся под случайное поле: она идёт в общий текст с
 * именем поля. */
export function layoutValidation(
  issues: readonly ValidationIssue[],
  knownFields: readonly string[],
  aliases: Readonly<Record<string, string>> = {},
): ValidationLayout {
  const byField = new Map<string, string[]>();
  const general: string[] = [];
  for (const issue of issues) {
    const field = issue.field === null ? null : (aliases[issue.field] ?? issue.field);
    if (field !== null && knownFields.includes(field)) {
      byField.set(field, [...(byField.get(field) ?? []), issue.message]);
    } else if (issue.field === null && issue.name !== null) {
      // Ошибка пути/запроса: имя параметра нужно, иначе «Значение должно быть целым числом.»
      // не говорит, о чём речь.
      general.push(`${issue.name}: ${issue.message}`);
    } else if (issue.field !== null) {
      general.push(`${issue.field}: ${issue.message}`);
    } else {
      general.push(issue.message);
    }
  }
  return {
    fieldErrors: [...byField].map(([field, messages]) => ({ field, message: messages.join(" ") })),
    generalError: general.length > 0 ? general.join(" ") : null,
  };
}

/** `detail` -> раскладка, если это ошибки валидации, иначе `null` (вызывающий разбирает строку
 * или запасное сообщение, как делал раньше). */
export function formValidation(
  detail: unknown,
  knownFields: readonly string[],
  aliases: Readonly<Record<string, string>> = {},
): ValidationLayout | null {
  const issues = validationIssues(detail);
  return issues === null ? null : layoutValidation(issues, knownFields, aliases);
}

/** Текст отказа для мест, где отдельных полей нет: строка `detail` как есть, а для ошибок
 * валидации - все их тексты одним сообщением. `null` - ни то ни другое (запасной текст места). */
export function detailText(detail: unknown): string | null {
  const text = stringDetail(detail);
  if (text !== null) {
    return text;
  }
  return formValidation(detail, [])?.generalError ?? null;
}
