import { useQueryClient } from "@tanstack/react-query";
import { Button, Space, Tree, Typography } from "antd";
import type { DataNode } from "antd/es/tree";
import { useMemo, useState } from "react";

import { apiClient } from "../api/client";
import { fieldError, stringDetail } from "../api/errors";
import { EmptyState, ErrorLoadingState, LoadingState } from "../app/states";
import { DepartmentHeadModal } from "../components/DepartmentHeadModal";
import { DepartmentNameModal } from "../components/DepartmentNameModal";
import type { DepartmentOut, DepartmentTreeNode } from "../entities/department";
import { buildDepartmentTree } from "../entities/department";
import { employeeNameById, resolveName } from "../entities/employee";
import type { EmployeeOut } from "../entities/employee";
import { useDepartmentsQuery } from "../entities/useDepartmentsQuery";
import { useAllEmployeesQuery } from "../entities/useEmployeesQuery";

/** Состояние открытого модального окна «Название»: `create` — новое
 * подразделение под `parentId` (`null` — в корне), `rename` — правка
 * уже существующего. `null` — окно закрыто. Одно состояние на оба
 * случая, а не два независимых булевых флага — окно всегда ровно одно
 * на экране, и по построению не может быть одновременно create и
 * rename. */
type NameModalState =
  | { kind: "create"; parentId: number | null }
  | { kind: "rename"; department: DepartmentOut }
  | null;

function toTreeData(
  nodes: DepartmentTreeNode[],
  managerNames: Map<number, string>,
  onAddChild: (parentId: number) => void,
  onRename: (department: DepartmentOut) => void,
  onAssignHead: (department: DepartmentOut) => void,
): DataNode[] {
  return nodes.map((node) => ({
    key: node.id,
    title: (
      <Space>
        <span>
          {node.name} — {resolveName(node.head_employee_id, managerNames)}
        </span>
        <Button
          size="small"
          type="link"
          onClick={(event) => {
            event.stopPropagation();
            onAddChild(node.id);
          }}
        >
          Добавить дочернее
        </Button>
        <Button
          size="small"
          type="link"
          onClick={(event) => {
            event.stopPropagation();
            onRename(node);
          }}
        >
          Переименовать
        </Button>
        <Button
          size="small"
          type="link"
          onClick={(event) => {
            event.stopPropagation();
            onAssignHead(node);
          }}
        >
          Назначить руководителя
        </Button>
      </Space>
    ),
    children: toTreeData(node.children, managerNames, onAddChild, onRename, onAssignHead),
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
 * сотрудников, `T-M1-19`).
 *
 * Создание — `POST /api/departments` (`name` + необязательный
 * `parent_id`; руководителя не передаём — схема его не принимает
 * намеренно, `department_service.py`). Переименование — `PUT` с ВСЕМИ
 * тремя полями (`name`/`parent_id`/`head_employee_id`), взятыми из уже
 * прочитанного `DepartmentOut`, а не только изменённым `name`:
 * `DepartmentUpdate` — полная замена, запрос, отправивший только `name`,
 * молча переносит подразделение в корень и снимает руководителя
 * (прецедент `T-M1-52`, тот же риск для паролей сотрудников). После
 * любой мутации — `invalidateQueries(["departments"])`, а не локальное
 * обновление: тот же запрос использует форма сотрудника и карточка
 * (`useDepartmentsQuery`). */
export function DepartmentsPage() {
  const queryClient = useQueryClient();
  const departmentsQuery = useDepartmentsQuery();
  const employeesQuery = useAllEmployeesQuery();
  const [nameModal, setNameModal] = useState<NameModalState>(null);
  const [headModalDepartment, setHeadModalDepartment] = useState<DepartmentOut | null>(null);

  const managerNames = useMemo(
    () => employeeNameById(employeesQuery.data ?? []),
    [employeesQuery.data],
  );
  // Только активные — сервер отклоняет уволенного руководителем
  // (`_require_active_head_reference`), предлагать заведомо
  // отклоняемый вариант в списке было бы ловушкой для пользователя.
  // Разрешение ФИО в дереве (`managerNames` выше) остаётся по ПОЛНОМУ
  // списку — это другая задача (не дать голому id остаться на экране
  // для уже назначенного, но затем уволенного руководителя).
  const activeEmployees: EmployeeOut[] = useMemo(
    () => (employeesQuery.data ?? []).filter((employee) => employee.employment_status === "active"),
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

  const handleCreateSubmit = async (name: string): Promise<string | null> => {
    if (nameModal?.kind !== "create") {
      return null;
    }
    const { error } = await apiClient.POST("/api/departments", {
      body: { name, parent_id: nameModal.parentId },
    });
    if (error) {
      return stringDetail(error.detail) ?? "Не удалось создать подразделение.";
    }
    await queryClient.invalidateQueries({ queryKey: ["departments"] });
    setNameModal(null);
    return null;
  };

  const handleRenameSubmit = async (name: string): Promise<string | null> => {
    if (nameModal?.kind !== "rename") {
      return null;
    }
    const department = nameModal.department;
    // Все три поля — см. докстринг компонента.
    const { error } = await apiClient.PUT("/api/departments/{department_id}", {
      params: { path: { department_id: department.id } },
      body: {
        name,
        parent_id: department.parent_id,
        head_employee_id: department.head_employee_id,
      },
    });
    if (error) {
      return stringDetail(error.detail) ?? "Не удалось переименовать подразделение.";
    }
    await queryClient.invalidateQueries({ queryKey: ["departments"] });
    setNameModal(null);
    return null;
  };

  const handleAssignHeadSubmit = async (
    headEmployeeId: number | null,
  ): Promise<{ field: string; message: string } | string | null> => {
    if (headModalDepartment === null) {
      return null;
    }
    // Все три поля — см. докстринг компонента.
    const { error } = await apiClient.PUT("/api/departments/{department_id}", {
      params: { path: { department_id: headModalDepartment.id } },
      body: {
        name: headModalDepartment.name,
        parent_id: headModalDepartment.parent_id,
        head_employee_id: headEmployeeId,
      },
    });
    if (error) {
      const matchedFieldError = fieldError(error.detail);
      if (matchedFieldError) {
        return matchedFieldError;
      }
      return stringDetail(error.detail) ?? "Не удалось назначить руководителя.";
    }
    await queryClient.invalidateQueries({ queryKey: ["departments"] });
    setHeadModalDepartment(null);
    return null;
  };

  return (
    <div>
      <Space align="center" style={{ width: "100%", justifyContent: "space-between" }}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          Подразделения
        </Typography.Title>
        <Button type="primary" onClick={() => setNameModal({ kind: "create", parentId: null })}>
          Добавить подразделение
        </Button>
      </Space>

      {departments.length === 0 ? (
        <EmptyState
          title="Подразделений нет"
          description="В системе пока не заведено ни одного подразделения."
          action={{
            label: "Добавить подразделение",
            onClick: () => setNameModal({ kind: "create", parentId: null }),
          }}
        />
      ) : (
        <Tree
          style={{ marginTop: 16 }}
          treeData={toTreeData(
            buildDepartmentTree(departments),
            managerNames,
            (parentId) => setNameModal({ kind: "create", parentId }),
            (department) => setNameModal({ kind: "rename", department }),
            (department) => setHeadModalDepartment(department),
          )}
          defaultExpandAll
          showLine
        />
      )}

      <DepartmentNameModal
        open={nameModal?.kind === "create"}
        title="Новое подразделение"
        submitLabel="Создать"
        onCancel={() => setNameModal(null)}
        onSubmit={handleCreateSubmit}
      />
      <DepartmentNameModal
        open={nameModal?.kind === "rename"}
        title="Переименовать подразделение"
        submitLabel="Сохранить"
        initialName={nameModal?.kind === "rename" ? nameModal.department.name : undefined}
        onCancel={() => setNameModal(null)}
        onSubmit={handleRenameSubmit}
      />
      <DepartmentHeadModal
        open={headModalDepartment !== null}
        departmentName={headModalDepartment?.name ?? ""}
        currentHeadId={headModalDepartment?.head_employee_id ?? null}
        employeeOptions={activeEmployees}
        onCancel={() => setHeadModalDepartment(null)}
        onSubmit={handleAssignHeadSubmit}
      />
    </div>
  );
}
