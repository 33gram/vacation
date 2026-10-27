import type { components } from "../api/generated/schema";

export type EmployeeOut = components["schemas"]["EmployeeOut"];

// Размеры экранов сотрудника для каркаса загрузки (`ui.md` §4): полей формы (в режиме
// создания по умолчанию, без поля пароля) и блоков карточки. Сверяются с отрисованным экраном
// тестами `EmployeeCreatePage.test.tsx`/`EmployeeCardPage.test.tsx`.
export const EMPLOYEE_FORM_FIELD_COUNT = 10;
export const EMPLOYEE_CARD_BLOCKS = 2;

// Единственное место для подписей значений `EmployeeOut` — были
// продублированы дословно в `EmployeesPage`/`EmployeeCardPage`
// (PR #153 review, п. 3).
export const WORK_SCHEDULE_LABELS: Record<string, string> = {
  standard: "Стандартный",
  shift: "Сменный",
};

export const EMPLOYMENT_STATUS_LABELS: Record<string, string> = {
  active: "Активен",
  terminated: "Уволен",
};

// Общий источник подписей для `EmployeeForm` (выбор способа входа) и
// просмотра карточки (`EmployeeCardPage`, T-M1-19 review, п. 5) — те же
// подписи в обоих местах, заданные один раз.
export const AUTH_TYPE_LABELS: Record<string, string> = {
  ldap: "Корпоративный каталог (LDAP)",
  local: "Локальный пароль",
};

/** Заглушка для нераскрытой ссылки (`department_id`/`manager_id`,
 * `T-M1-19`) — используется и для «вершины иерархии» (руководителя
 * действительно нет, `manager_id: null`, `FR-EMP-001`), и для случая,
 * когда id не нашёлся в загруженном списке: различать эти два случая в
 * тексте не требуется — оба означают «здесь нечего показать по имени», а
 * не голый идентификатор. */
export const UNRESOLVED_REFERENCE_PLACEHOLDER = "—";

export function employeeNameById(employees: EmployeeOut[]): Map<number, string> {
  return new Map(employees.map((employee) => [employee.id, employee.full_name]));
}

export function resolveName(id: number | null, namesById: Map<number, string>): string {
  if (id === null) {
    return UNRESOLVED_REFERENCE_PLACEHOLDER;
  }
  return namesById.get(id) ?? UNRESOLVED_REFERENCE_PLACEHOLDER;
}
