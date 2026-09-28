import type { components } from "../api/generated/schema";

export type EmployeeOut = components["schemas"]["EmployeeOut"];

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
