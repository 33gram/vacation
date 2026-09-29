import type { components } from "../api/generated/schema";

export type DepartmentOut = components["schemas"]["DepartmentOut"];

export function departmentNameById(departments: DepartmentOut[]): Map<number, string> {
  return new Map(departments.map((department) => [department.id, department.name]));
}

export interface DepartmentTreeNode extends DepartmentOut {
  children: DepartmentTreeNode[];
}

/** Строит дерево из плоского списка `GET /api/departments` (`parent_id`,
 * `T-M1-20`). Написана заново для этого экрана, не восстановлена из
 * истории (`entities/department.ts` уже содержала `buildDepartmentTree`,
 * удалённую в `T-M1-19` как мёртвый код с докстрингом, ошибочно
 * утверждавшим, что ею пользуется форма сотрудника, — не пользовалась;
 * прежний код здесь не смотрели).
 *
 * Одно свойство сохранено осознанно: узел, чей `parent_id` не нашёлся
 * среди полученных подразделений (сирота — например, родитель удалён
 * прямой правкой БД в обход API, у которого эндпоинта удаления нет
 * вовсе), становится КОРНЕМ, а не пропадает из дерева — тот же путь,
 * что и для настоящего `parent_id: null`, поскольку различать их в
 * отображении незачем: обоим нечего показать как родителя. Проверено
 * тестом на сумму узлов по всем уровням: она равна длине исходного
 * списка при любом входе, включая осиротевший `parent_id`. */
export function buildDepartmentTree(departments: DepartmentOut[]): DepartmentTreeNode[] {
  const nodeById = new Map<number, DepartmentTreeNode>(
    departments.map((department) => [department.id, { ...department, children: [] }]),
  );

  const roots: DepartmentTreeNode[] = [];
  for (const department of departments) {
    const node = nodeById.get(department.id);
    if (node === undefined) {
      // Не может произойти: `node` строится из того же `departments`,
      // которым мы сейчас итерируемся.
      continue;
    }
    const parent = department.parent_id !== null ? nodeById.get(department.parent_id) : undefined;
    if (parent !== undefined) {
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }
  return roots;
}
