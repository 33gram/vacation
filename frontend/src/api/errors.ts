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
// префикс `"<поле>: "` (T-M1-47, T-M2-27) — по нему интерфейс кладёт ошибку под
// нужное поле формы, а сам префикс пользователю не показывается
// (`ui.md` §7: место ошибки — под полем). Список ЗАКРЫТ и охраняется
// backend-тестом `test_field_error_prefixes_guard.py`: он обнаруживает префиксы в `app/**`
// разбором синтаксического дерева и сверяет их с этим перечнем в обе стороны, поэтому
// новое поле с этим же соглашением придётся добавить сюда явно, а запись без источника на
// бэкенде тест покажет. Молчаливого совпадения по любому `"слово: "` в начале строки нет:
// текст ошибки не обязан быть в этой форме (например, `WeakPasswordError` начинается с
// "Пароль", а не с имени поля). ГРАНИЦА сторожа: он видит строки-литералы (и начало
// f-строки); ключи реестра бизнес-правил (`RuleValuesInvalidError`) собираются во время
// выполнения и сюда не входят — их разбор идёт по ключам из ответа `GET`.
const FIELD_ERROR_PREFIXES = [
  "manager_id",
  "email",
  "personnel_number",
  "department_id",
  "head_employee_id",
  "code",
  "is_active",
  "balance_tracked",
] as const;

/** ЕДИНСТВЕННЫЙ разбор префиксов полей (`T-M2-27`). Текст — один или несколько отказов вида
 * `<поле>: текст`, склеенных через `"; "` (`RuleValuesInvalidError`, `MainLeaveTypeProtectedError`).
 * Возвращает ВСЕ совпавшие префиксы в порядке появления; `null` — строка не в этой форме: не
 * строка, нет известных полей, строка не НАЧИНАЕТСЯ с известного поля (текст до первого
 * префикса или неизвестное поле), и вызывающий показывает её целиком общим текстом.
 * Неизвестное поле внутри строки в разбор не попадает и остаётся хвостом предыдущего текста;
 * `"; "` внутри текста не разделитель, если за ним не стоит известный префикс. */
export function prefixedMessages(
  detail: unknown,
  knownFields: readonly string[],
): { field: string; message: string }[] | null {
  const text = stringDetail(detail);
  if (text === null || knownFields.length === 0) {
    return null;
  }
  const alternatives = [...knownFields]
    .sort((a, b) => b.length - a.length)
    .map((field) => field.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("|");
  const starts = [...text.matchAll(new RegExp(`(?:^|; )(${alternatives}): `, "g"))];
  if (starts.length === 0 || starts[0].index !== 0) {
    return null;
  }
  return starts.map((match, index) => {
    const textStart = match.index + match[0].length;
    const textEnd = index + 1 < starts.length ? starts[index + 1].index : text.length;
    return { field: match[1], message: text.slice(textStart, textEnd) };
  });
}

/** Разбирает `stringDetail`-совместимый текст на `{field, message}`, если
 * он начинается с одного из известных префиксов, или возвращает `null`
 * (не наш случай — общая ошибка операции, `ui.md` §7). Разбор — `prefixedMessages`;
 * сообщение — весь остаток строки после первого префикса. */
export function fieldError(detail: unknown): { field: string; message: string } | null {
  const parsed = prefixedMessages(detail, FIELD_ERROR_PREFIXES);
  const text = stringDetail(detail);
  if (parsed === null || text === null) {
    return null;
  }
  const { field } = parsed[0];
  return { field, message: text.slice(`${field}: `.length) };
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

export interface ValidationLayout<F extends string = string> {
  /** По одной записи на поле формы; несколько нарушений одного поля идут одним текстом. */
  fieldErrors: { field: F; message: string }[];
  /** Всё, что не легло под поле: ошибка на всём теле, ошибка пути/запроса и ошибка поля, которого
   * в форме нет. Показывается общим текстом над кнопками (`ui.md` §7). `null` - нечего показывать. */
  generalError: string | null;
}

/** Раскладывает ВСЕ ошибки сразу (`ui.md` §7), не первую. `knownFields` - имена полей ЭТОЙ формы;
 * `aliases` - когда поле формы называется иначе, чем поле тела (`initial_password` -> `password`).
 * Ошибка поля, которого в форме нет, не кладётся под случайное поле: она идёт в общий текст с
 * именем поля. */
export function layoutValidation<F extends string>(
  issues: readonly ValidationIssue[],
  knownFields: readonly F[],
  aliases: Readonly<Record<string, F>> = {},
): ValidationLayout<F> {
  const byField = new Map<F, string[]>();
  const general: string[] = [];
  for (const issue of issues) {
    const field = issue.field === null ? null : (aliases[issue.field] ?? issue.field);
    if (field !== null && (knownFields as readonly string[]).includes(field)) {
      byField.set(field as F, [...(byField.get(field as F) ?? []), issue.message]);
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
export function formValidation<F extends string>(
  detail: unknown,
  knownFields: readonly F[],
  aliases: Readonly<Record<string, F>> = {},
): ValidationLayout<F> | null {
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

/** Для мест с ОДНИМ полем ввода (модальные окна «Подразделение»: имя, руководитель): текст ошибок
 * этого поля, а если в ответе есть и другие - они в конце тем же текстом; `null` - не ошибки
 * валидации. */
export function singleFieldValidationText(detail: unknown, field: string): string | null {
  const layout = formValidation(detail, [field]);
  if (layout === null) {
    return null;
  }
  return [...layout.fieldErrors.map((item) => item.message), layout.generalError]
    .filter((text): text is string => text !== null)
    .join(" ");
}

/** Исключение для `mutationFn`, которые бросают ошибку наружу: несёт раскладку по полям, чтобы
 * `onError` мог положить её под поля, а не только показать текстом. `message` - общий текст. */
export class ValidationFailure extends Error {
  readonly layout: ValidationLayout;

  constructor(layout: ValidationLayout) {
    super(layout.generalError ?? "");
    this.layout = layout;
  }
}
