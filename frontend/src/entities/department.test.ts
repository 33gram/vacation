import { describe, expect, it } from "vitest";

import { departmentResponse } from "../test-utils/fixtures";
import { buildDepartmentTree, departmentUpdateBody } from "./department";
import type { DepartmentTreeNode } from "./department";

function countNodes(tree: DepartmentTreeNode[]): number {
  return tree.reduce((sum, node) => sum + 1 + countNodes(node.children), 0);
}

describe("buildDepartmentTree (T-M1-20)", () => {
  it("returns an empty tree for an empty list", () => {
    expect(buildDepartmentTree([])).toEqual([]);
  });

  it("nests several levels deep by parent_id", () => {
    const root = departmentResponse({ id: 1, name: "Компания", parent_id: null });
    const division = departmentResponse({ id: 2, name: "Дивизион", parent_id: 1 });
    const department = departmentResponse({ id: 3, name: "Отдел", parent_id: 2 });
    const team = departmentResponse({ id: 4, name: "Команда", parent_id: 3 });

    const tree = buildDepartmentTree([root, division, department, team]);

    expect(tree).toHaveLength(1);
    expect(tree[0].id).toBe(1);
    expect(tree[0].children).toHaveLength(1);
    expect(tree[0].children[0].id).toBe(2);
    expect(tree[0].children[0].children).toHaveLength(1);
    expect(tree[0].children[0].children[0].id).toBe(3);
    expect(tree[0].children[0].children[0].children).toHaveLength(1);
    expect(tree[0].children[0].children[0].children[0].id).toBe(4);
    expect(tree[0].children[0].children[0].children[0].children).toEqual([]);
  });

  it("makes a sibling subtree sit next to another root, not nested under it", () => {
    const salesRoot = departmentResponse({ id: 1, name: "Продажи", parent_id: null });
    const salesTeam = departmentResponse({ id: 2, name: "Команда продаж", parent_id: 1 });
    const hrRoot = departmentResponse({ id: 3, name: "HR", parent_id: null });

    const tree = buildDepartmentTree([salesRoot, salesTeam, hrRoot]);

    expect(tree.map((node) => node.id)).toEqual([1, 3]);
    expect(tree[0].children.map((node) => node.id)).toEqual([2]);
    expect(tree[1].children).toEqual([]);
  });

  it("treats a node whose parent_id points nowhere as a root, not a dropped node", () => {
    const orphan = departmentResponse({ id: 5, name: "Осиротевший отдел", parent_id: 999 });
    const other = departmentResponse({ id: 6, name: "Обычный отдел", parent_id: null });

    const tree = buildDepartmentTree([orphan, other]);

    expect(tree.map((node) => node.id).sort()).toEqual([5, 6]);
  });

  it("keeps the total node count across all levels equal to the input length, for a plain list, a nested tree and an orphan", () => {
    const inputs = [
      [],
      [departmentResponse({ id: 1, parent_id: null })],
      [
        departmentResponse({ id: 1, parent_id: null }),
        departmentResponse({ id: 2, parent_id: 1 }),
        departmentResponse({ id: 3, parent_id: 2 }),
      ],
      [
        departmentResponse({ id: 1, parent_id: null }),
        departmentResponse({ id: 2, parent_id: 999 }), // сирота
      ],
    ];

    for (const departments of inputs) {
      expect(countNodes(buildDepartmentTree(departments))).toBe(departments.length);
    }
  });
});

describe("departmentUpdateBody (T-M1-20, PR #157 review)", () => {
  const department = departmentResponse({
    id: 1,
    name: "Отдел",
    parent_id: 5,
    head_employee_id: 9,
  });

  it("always returns exactly three keys, even with no overrides at all", () => {
    const body = departmentUpdateBody(department);

    expect(Object.keys(body).sort()).toEqual(["head_employee_id", "name", "parent_id"]);
    expect(body).toEqual({ name: "Отдел", parent_id: 5, head_employee_id: 9 });
  });

  it("overrides only the given field, keeping the other two from the department", () => {
    expect(departmentUpdateBody(department, { name: "Новое имя" })).toEqual({
      name: "Новое имя",
      parent_id: 5,
      head_employee_id: 9,
    });
  });

  it("an override of null for parent_id is honored, not treated as absent (move to root)", () => {
    const body = departmentUpdateBody(department, { parent_id: null });

    expect(body).toEqual({ name: "Отдел", parent_id: null, head_employee_id: 9 });
  });

  it("an override of null for head_employee_id is honored, not treated as absent (unassign the head)", () => {
    const body = departmentUpdateBody(department, { head_employee_id: null });

    expect(body).toEqual({ name: "Отдел", parent_id: 5, head_employee_id: null });
  });

  it("applies more than one override at once", () => {
    const body = departmentUpdateBody(department, { parent_id: null, head_employee_id: null });

    expect(body).toEqual({ name: "Отдел", parent_id: null, head_employee_id: null });
  });
});
