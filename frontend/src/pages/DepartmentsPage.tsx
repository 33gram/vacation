import { Tree, Typography } from "antd";
import type { DataNode } from "antd/es/tree";
import { useMemo } from "react";

import { EmptyState, ErrorLoadingState, LoadingState } from "../app/states";
import type { DepartmentTreeNode } from "../entities/department";
import { buildDepartmentTree } from "../entities/department";
import { employeeNameById, resolveName } from "../entities/employee";
import { useDepartmentsQuery } from "../entities/useDepartmentsQuery";
import { useAllEmployeesQuery } from "../entities/useEmployeesQuery";

function toTreeData(nodes: DepartmentTreeNode[], managerNames: Map<number, string>): DataNode[] {
  return nodes.map((node) => ({
    key: node.id,
    title: `${node.name} — ${resolveName(node.head_employee_id, managerNames)}`,
    children: toTreeData(node.children, managerNames),
  }));
}

/** Раздел «Подразделения» (`T-M1-20`, `FR-EMP-002`). Дерево не подпадает
 * ни под один из четырёх типов экрана (`ui.md` §3 — сказано в самой
 * задаче), но состояния §4 общие для любого экрана.
 *
 * Руководитель в узле разрешается по ПОЛНОМУ списку сотрудников
 * (`useAllEmployeesQuery`, `include_terminated: true`), а не по видимому
 * по умолчанию: иначе уволенный руководитель отображался бы прочерком —
 * неотличимо от «руководителя нет» (та же ошибка, что правили в списке
 * сотрудников, `T-M1-19`). */
export function DepartmentsPage() {
  const departmentsQuery = useDepartmentsQuery();
  const employeesQuery = useAllEmployeesQuery();

  const managerNames = useMemo(
    () => employeeNameById(employeesQuery.data ?? []),
    [employeesQuery.data],
  );

  if (departmentsQuery.isLoading || employeesQuery.isLoading) {
    return <LoadingState />;
  }
  if (departmentsQuery.isError) {
    return <ErrorLoadingState onRetry={() => departmentsQuery.refetch()} />;
  }
  if (employeesQuery.isError) {
    return <ErrorLoadingState onRetry={() => employeesQuery.refetch()} />;
  }

  const departments = departmentsQuery.data ?? [];

  return (
    <div>
      <Typography.Title level={3}>Подразделения</Typography.Title>

      {departments.length === 0 ? (
        <EmptyState
          title="Подразделений нет"
          description="В системе пока не заведено ни одного подразделения."
        />
      ) : (
        <Tree
          treeData={toTreeData(buildDepartmentTree(departments), managerNames)}
          defaultExpandAll
          showLine
        />
      )}
    </div>
  );
}
