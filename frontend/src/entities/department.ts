import type { components } from "../api/generated/schema";

export type DepartmentOut = components["schemas"]["DepartmentOut"];

export function departmentNameById(departments: DepartmentOut[]): Map<number, string> {
  return new Map(departments.map((department) => [department.id, department.name]));
}
