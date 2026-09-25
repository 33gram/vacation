import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Descriptions, Row, Col, Space, Typography } from "antd";
import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { apiClient } from "../api/client";
import { fieldError, stringDetail, violationList } from "../api/errors";
import { formatDate } from "../app/format";
import { ErrorLoadingState, LoadingState } from "../app/states";
import { EmployeeForm } from "../components/EmployeeForm";
import type { EmployeeFormSubmitError, EmployeeFormValues } from "../components/EmployeeForm";
import { departmentNameById } from "../entities/department";
import { employeeNameById, resolveName } from "../entities/employee";

const WORK_SCHEDULE_LABELS: Record<string, string> = {
  standard: "Стандартный",
  shift: "Сменный",
};

const EMPLOYMENT_STATUS_LABELS: Record<string, string> = {
  active: "Активен",
  terminated: "Уволен",
};

function useEmployeeQuery(employeeId: number) {
  return useQuery({
    queryKey: ["employee", employeeId],
    queryFn: async () => {
      const { data, response } = await apiClient.GET("/api/employees/{employee_id}", {
        params: { path: { employee_id: employeeId } },
      });
      if (!data) {
        throw new Error(`GET /api/employees/${employeeId} failed with status ${response.status}`);
      }
      return data;
    },
  });
}

function useAllEmployeesQuery() {
  return useQuery({
    queryKey: ["employees", true],
    queryFn: async () => {
      const { data, response } = await apiClient.GET("/api/employees", {
        params: { query: { include_terminated: true } },
      });
      if (!data) {
        throw new Error(`GET /api/employees failed with status ${response.status}`);
      }
      return data;
    },
  });
}

function useDepartmentsQuery() {
  return useQuery({
    queryKey: ["departments"],
    queryFn: async () => {
      const { data, response } = await apiClient.GET("/api/departments");
      if (!data) {
        throw new Error(`GET /api/departments failed with status ${response.status}`);
      }
      return data;
    },
  });
}

/** «Карточка записи» (`ui.md` §3, `T-M1-19`) — просмотр и правка в одном
 * месте: режим правки не отдельный маршрут, а переключение содержимого
 * ЭТОЙ страницы (`isEditing`). Руководитель разрешается по ПОЛНОМУ списку
 * сотрудников (`include_terminated: true`), а не по тому же списку, что
 * показывает `EmployeesPage` по умолчанию: руководитель может сам быть
 * уволен, но его ФИО в карточке подчинённого всё равно должно разрешиться
 * по имени, а не остаться голым id (то же самое верно и для выбора
 * руководителя в форме правки — уволенных отсеивает сервис, а не список). */
export function EmployeeCardPage() {
  const { id } = useParams<{ id: string }>();
  const employeeId = Number(id);
  const queryClient = useQueryClient();
  const [isEditing, setIsEditing] = useState(false);

  const employeeQuery = useEmployeeQuery(employeeId);
  const employeesQuery = useAllEmployeesQuery();
  const departmentsQuery = useDepartmentsQuery();

  const managerNames = useMemo(
    () => employeeNameById(employeesQuery.data ?? []),
    [employeesQuery.data],
  );
  const departmentNames = useMemo(
    () => departmentNameById(departmentsQuery.data ?? []),
    [departmentsQuery.data],
  );

  if (employeeQuery.isLoading || employeesQuery.isLoading || departmentsQuery.isLoading) {
    return <LoadingState />;
  }
  if (employeeQuery.isError) {
    return <ErrorLoadingState onRetry={() => employeeQuery.refetch()} />;
  }
  if (employeesQuery.isError) {
    return <ErrorLoadingState onRetry={() => employeesQuery.refetch()} />;
  }
  if (departmentsQuery.isError) {
    return <ErrorLoadingState onRetry={() => departmentsQuery.refetch()} />;
  }

  const employee = employeeQuery.data;
  if (!employee) {
    return null;
  }

  const statusLabel = EMPLOYMENT_STATUS_LABELS[employee.employment_status] ?? employee.employment_status;
  const statusText =
    employee.employment_status === "terminated" && employee.termination_date
      ? `${statusLabel} с ${formatDate(employee.termination_date)}`
      : statusLabel;

  const handleSubmit = async (values: EmployeeFormValues): Promise<EmployeeFormSubmitError | null> => {
    // PUT — полная замена (`EmployeeUpdate`): отправляется ВЕСЬ состав
    // полей формы, а не только изменённое — `EmployeeForm.handleFinish`
    // строит `values` из формы целиком, здесь ничего не подмешивается и
    // ничего не убирается.
    const { data, error } = await apiClient.PUT("/api/employees/{employee_id}", {
      params: { path: { employee_id: employeeId } },
      body: {
        full_name: values.full_name,
        personnel_number: values.personnel_number,
        department_id: values.department_id,
        position: values.position,
        hire_date: values.hire_date,
        manager_id: values.manager_id,
        email: values.email,
        work_schedule_type: values.work_schedule_type,
        leave_category: values.leave_category,
        auth_type: values.auth_type,
        new_password: values.password,
      },
    });

    if (error) {
      const matchedFieldError = fieldError(error.detail);
      const violations = violationList(error.detail);
      return {
        fieldErrors: matchedFieldError ? [matchedFieldError] : [],
        violatedCodes: violations ? new Set(violations.map((violation) => violation.code)) : null,
        generalError:
          !matchedFieldError && !violations
            ? (stringDetail(error.detail) ?? "Не удалось сохранить карточку.")
            : null,
      };
    }

    await queryClient.setQueryData(["employee", employeeId], data);
    await queryClient.invalidateQueries({ queryKey: ["employees"] });
    setIsEditing(false);
    return null;
  };

  return (
    <div>
      <Space align="center" style={{ width: "100%", justifyContent: "space-between" }}>
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            {employee.full_name}
          </Typography.Title>
          <Typography.Text type={employee.employment_status === "terminated" ? "danger" : "success"}>
            {statusText}
          </Typography.Text>
        </div>
        {!isEditing && (
          <Button onClick={() => setIsEditing(true)}>Редактировать</Button>
        )}
      </Space>

      {isEditing ? (
        <div style={{ marginTop: 24, maxWidth: 480 }}>
          <EmployeeForm
            mode="edit"
            initialValues={employee}
            departments={departmentsQuery.data ?? []}
            managerOptions={employeesQuery.data ?? []}
            onSubmit={handleSubmit}
          />
          <Button onClick={() => setIsEditing(false)}>Отмена</Button>
        </div>
      ) : (
        <Row gutter={32} style={{ marginTop: 24 }}>
          <Col span={12}>
            <Descriptions column={1} title="Организационные данные" bordered size="small">
              <Descriptions.Item label="Табельный номер">
                <span style={{ fontVariantNumeric: "tabular-nums" }}>{employee.personnel_number}</span>
              </Descriptions.Item>
              <Descriptions.Item label="Подразделение">
                {resolveName(employee.department_id, departmentNames)}
              </Descriptions.Item>
              <Descriptions.Item label="Должность">{employee.position}</Descriptions.Item>
              <Descriptions.Item label="Дата приёма">{formatDate(employee.hire_date)}</Descriptions.Item>
              <Descriptions.Item label="Руководитель">
                {resolveName(employee.manager_id, managerNames)}
              </Descriptions.Item>
            </Descriptions>
          </Col>
          <Col span={12}>
            <Descriptions column={1} title="Условия работы" bordered size="small">
              <Descriptions.Item label="E-mail">{employee.email}</Descriptions.Item>
              <Descriptions.Item label="График работы">
                {WORK_SCHEDULE_LABELS[employee.work_schedule_type] ?? employee.work_schedule_type}
              </Descriptions.Item>
              <Descriptions.Item label="Категория для расчёта">
                {employee.leave_category ?? "—"}
              </Descriptions.Item>
            </Descriptions>
          </Col>
        </Row>
      )}
    </div>
  );
}
