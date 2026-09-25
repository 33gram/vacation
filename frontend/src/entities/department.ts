import type { components } from "../api/generated/schema";

export type DepartmentOut = components["schemas"]["DepartmentOut"];

export interface DepartmentTreeNode {
  department: DepartmentOut;
  children: DepartmentTreeNode[];
}

/** Строит дерево подразделений из плоского списка (`GET /api/departments`,
 * `parent_id`) — общая логика для `T-M1-19` (выбор подразделения в форме
 * сотрудника) и `T-M1-20` (сам раздел «Подразделения», drag-and-drop), а
 * не обход дерева, написанный дважды.
 *
 * Узел, чей `parent_id` не находится среди переданных подразделений (не
 * должно происходить при исправных данных, но опережающая проверка на
 * сервере — не гарантия от рассинхронизации набора, например при частично
 * применённом фильтре), становится корнем, а не отбрасывается — дерево
 * остаётся полным (сумма узлов на всех уровнях равна длине исходного
 * списка), а не молча теряет часть подразделений. */
export function buildDepartmentTree(departments: DepartmentOut[]): DepartmentTreeNode[] {
  const nodesById = new Map<number, DepartmentTreeNode>(
    departments.map((department) => [department.id, { department, children: [] }]),
  );
  const roots: DepartmentTreeNode[] = [];

  for (const node of nodesById.values()) {
    const parentId = node.department.parent_id;
    const parent = parentId !== null ? nodesById.get(parentId) : undefined;
    if (parent) {
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }

  return roots;
}

export function departmentNameById(departments: DepartmentOut[]): Map<number, string> {
  return new Map(departments.map((department) => [department.id, department.name]));
}
