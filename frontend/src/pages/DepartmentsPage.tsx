import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Space, Tree, Typography } from "antd";
import type { DataNode, TreeProps } from "antd/es/tree";
import { useMemo, useState } from "react";

import { apiClient } from "../api/client";
import { detailText, fieldError, singleFieldValidationText, withoutFieldPrefixes } from "../api/errors";
import { EmptyState, ErrorLoadingState, LoadingState } from "../app/states";
import { DepartmentHeadModal } from "../components/DepartmentHeadModal";
import { DepartmentNameModal } from "../components/DepartmentNameModal";
import type { DepartmentOut, DepartmentTreeNode } from "../entities/department";
import { buildDepartmentTree, departmentUpdateBody } from "../entities/department";
import { employeeNameById, resolveName } from "../entities/employee";
import type { EmployeeOut } from "../entities/employee";
import { useDepartmentsQuery } from "../entities/useDepartmentsQuery";
import { useAllEmployeesQuery } from "../entities/useEmployeesQuery";

// Оценка числа узлов дерева для каркаса загрузки (данных ещё нет, постраничной выдачи у дерева нет).
const DEPARTMENTS_SKELETON_ROWS = 8;

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

/** `DataNode` антда не несёт доменных данных — расширяем его исходным
 * `DepartmentOut` узла, чтобы `onDrop` (ниже) мог прочитать `id`/
 * `parent_id` перетаскиваемого и целевого узла напрямую из `info.
 * dragNode`/`info.node`, а не искать их по `key` в отдельной карте. */
interface DepartmentDataNode extends DataNode {
  department: DepartmentOut;
}

function toTreeData(
  nodes: DepartmentTreeNode[],
  managerNames: Map<number, string>,
  onAddChild: (parentId: number) => void,
  onRename: (department: DepartmentOut) => void,
  onAssignHead: (department: DepartmentOut) => void,
): DepartmentDataNode[] {
  return nodes.map((node) => ({
    key: node.id,
    department: node,
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

/** Раздел «Подразделения» (`T-M1-20`, `FR-EMP-002`). Экран типа «Дерево»
 * (`ui.md` §3, пятый тип, `T-M2-26`); состояния §4 общие для любого экрана.
 *
 * Руководитель в узле разрешается по ПОЛНОМУ списку сотрудников
 * (`useAllEmployeesQuery`, `include_terminated: true`), а не по видимому
 * по умолчанию: иначе уволенный руководитель отображался бы прочерком —
 * неотличимо от «руководителя нет» (та же ошибка, что правили в списке
 * сотрудников, `T-M1-19`).
 *
 * Создание — `POST /api/departments` (`name` + необязательный
 * `parent_id`; руководителя не передаём — схема его не принимает
 * намеренно, `department_service.py`). Переименование, назначение
 * руководителя и перенос узла — все трое `PUT` с ВСЕМИ тремя полями
 * (`name`/`parent_id`/`head_employee_id`): `DepartmentUpdate` — полная
 * замена, запрос, отправивший только изменившееся поле, молча переносит
 * подразделение в корень и снимает руководителя (прецедент `T-M1-52`,
 * тот же риск для паролей сотрудников). Тело собирает ОДИН помощник,
 * `departmentUpdateBody` (`entities/department.ts`), а не три ручные
 * копии одного и того же литерала (PR #157 review, замечание 2) — тот
 * же риск однажды забыть поле, только теперь в трёх местах вместо
 * одного. После любой мутации — `invalidateQueries(["departments"])`, а
 * не локальное обновление: тот же запрос использует форма сотрудника и
 * карточка (`useDepartmentsQuery`). */
export function DepartmentsPage() {
  const queryClient = useQueryClient();
  const departmentsQuery = useDepartmentsQuery();
  const employeesQuery = useAllEmployeesQuery();
  const [nameModal, setNameModal] = useState<NameModalState>(null);
  const [headModalDepartment, setHeadModalDepartment] = useState<DepartmentOut | null>(null);
  const [reparentError, setReparentError] = useState<string | null>(null);

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

  // Перенос узла (`T-M1-20`, коммит 4) — оптимистичный: `treeData` строится
  // из кэша `["departments"]` (`useDepartmentsQuery`), поэтому дерево
  // «переезжает» сразу по клику, без ожидания ответа сервера — `onMutate`
  // правит кэш локально, `onError` возвращает снятый снимок, если сервер
  // отказал (цикл, несуществующий родитель — правила целостности НЕ
  // дублируются на клиенте, правило 5: текст ошибки — ровно тот, что
  // вернул сервер). Без `onMutate`/`onError`, симметричных друг другу,
  // дерево осталось бы в отклонённом сервером положении до следующей
  // непричастной инвалидации кэша — то есть врало бы о структуре.
  // `onSettled` — `invalidateQueries` в любом случае, чтобы подтверждённое
  // сервером состояние (успех) тоже дошло, а не осталось равным
  // оптимистичной догадке (например, если бы сервер сам изменил что-то
  // ещё, о чём клиент не знает).
  const reparentMutation = useMutation({
    mutationFn: async ({
      department,
      newParentId,
    }: {
      department: DepartmentOut;
      newParentId: number | null;
    }) => {
      const { error } = await apiClient.PUT("/api/departments/{department_id}", {
        params: { path: { department_id: department.id } },
        body: departmentUpdateBody(department, { parent_id: newParentId }),
      });
      if (error) {
        throw new Error(detailText(error.detail) ?? "Не удалось перенести подразделение.");
      }
    },
    onMutate: async ({ department, newParentId }) => {
      setReparentError(null);
      await queryClient.cancelQueries({ queryKey: ["departments"] });
      const previous = queryClient.getQueryData<DepartmentOut[]>(["departments"]);
      queryClient.setQueryData<DepartmentOut[]>(["departments"], (old) =>
        (old ?? []).map((existing) =>
          existing.id === department.id ? { ...existing, parent_id: newParentId } : existing,
        ),
      );
      return { previous };
    },
    onError: (error, _variables, context) => {
      if (context?.previous) {
        queryClient.setQueryData(["departments"], context.previous);
      }
      setReparentError(error instanceof Error ? error.message : "Не удалось перенести подразделение.");
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["departments"] });
    },
  });

  if (departmentsQuery.isLoading || employeesQuery.isLoading) {
    // ОЦЕНКА, не сверяется с итогом (`ui.md` §3, тип «Дерево»): взята таблица из одной колонки;
    // число узлов зависит от данных, которых до загрузки нет.
    return <LoadingState shape={{ kind: "table", rows: DEPARTMENTS_SKELETON_ROWS, columns: 1 }} />;
  }
  if (departmentsQuery.isError) {
    return <ErrorLoadingState onRetry={() => departmentsQuery.refetch()} />;
  }
  if (employeesQuery.isError) {
    return <ErrorLoadingState onRetry={() => employeesQuery.refetch()} />;
  }

  const departments = departmentsQuery.data ?? [];

  // Бросок узла на самого себя — единственное, что проверяется на
  // клиенте (правило 5 не запрещает то, что не является правилом
  // целостности само по себе, а тривиальным no-op'ом): цикл и
  // несуществующего родителя проверяет сервер, текст его отказа
  // показывается как есть (см. `onError` мутации выше).
  //
  // `dropToGap` — перенос в корень (`T-M1-20`, коммит 4, задача просит
  // решить и назвать способ): бросок МЕЖДУ узлами одного уровня (не НА
  // узел) переносит перетаскиваемый узел туда же, где уже находится
  // целевой — берётся `parent_id` целевого узла, а не его `id`. Для
  // целевого узла КОРНЕВОГО уровня это `null` — ровно перенос в корень,
  // без отдельной выделенной зоны сброса: гэп между корневыми узлами и
  // ЕСТЬ этот случай, других не потребовалось.
  const handleDrop: TreeProps<DepartmentDataNode>["onDrop"] = (info) => {
    const dragged = info.dragNode.department;
    const target = info.node.department;
    if (dragged.id === target.id) {
      return;
    }
    const newParentId = info.dropToGap ? target.parent_id : target.id;
    reparentMutation.mutate({ department: dragged, newParentId });
  };

  const handleCreateSubmit = async (name: string): Promise<string | null> => {
    if (nameModal?.kind !== "create") {
      return null;
    }
    const { error } = await apiClient.POST("/api/departments", {
      body: { name, parent_id: nameModal.parentId },
    });
    if (error) {
      // 422 (`T-M2-28`): у окна одно поле, тексты ошибок валидации показываются в окне (не запасным текстом).
      return (
        singleFieldValidationText(error.detail, "name") ??
        withoutFieldPrefixes(error.detail) ??
        "Не удалось создать подразделение."
      );
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
    const { error } = await apiClient.PUT("/api/departments/{department_id}", {
      params: { path: { department_id: department.id } },
      body: departmentUpdateBody(department, { name }),
    });
    if (error) {
      return (
        singleFieldValidationText(error.detail, "name") ??
        withoutFieldPrefixes(error.detail) ??
        "Не удалось переименовать подразделение."
      );
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
    const { error } = await apiClient.PUT("/api/departments/{department_id}", {
      params: { path: { department_id: headModalDepartment.id } },
      body: departmentUpdateBody(headModalDepartment, { head_employee_id: headEmployeeId }),
    });
    if (error) {
      const matchedFieldError = fieldError(error.detail, ["head_employee_id"]);
      if (matchedFieldError) {
        return matchedFieldError;
      }
      const validationText = singleFieldValidationText(error.detail, "head_employee_id");
      if (validationText !== null) {
        return { field: "head_employee_id", message: validationText };
      }
      return withoutFieldPrefixes(error.detail) ?? "Не удалось назначить руководителя.";
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

      {reparentError && (
        <Alert
          type="error"
          message={reparentError}
          showIcon
          closable
          onClose={() => setReparentError(null)}
          style={{ marginTop: 16 }}
        />
      )}

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
        <Tree<DepartmentDataNode>
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
          // Пока перенос не завершился, дерево не перетаскивается вовсе
          // (PR #157 review, замечание 1) — не ранний выход внутри
          // `handleDrop`: у того же эффекта («второй бросок ничего не
          // делает») не было бы никакого видимого пользователю сигнала —
          // курсор показывал бы обычное перетаскивание, `drop`
          // случался бы, и человек не понял бы, почему ничего не
          // произошло. `draggable={false}` снимает у узлов сам HTML-
          // атрибут (`rc-tree`, `TreeNode.js`: `isDraggable` завязан на
          // `context.draggable`, обработчики `onDragStart`/`onDrop` не
          // навешиваются вовсе, когда он ложный) — второе перетаскивание
          // видимо не начинается, а не начинается и молча ничего не
          // отправляет.
          draggable={reparentMutation.isPending ? false : { icon: false }}
          onDrop={handleDrop}
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
