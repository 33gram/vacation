import { useQuery } from "@tanstack/react-query";
import { Descriptions, Row, Col, Typography } from "antd";
import { useMemo } from "react";
import { useParams } from "react-router-dom";

import { apiClient } from "../api/client";
import { formatDate } from "../app/format";
import { ErrorLoadingState, LoadingState } from "../app/states";
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

/** «Карточка записи» (`ui.md` §3), только просмотр — правка и создание
 * приходят следующим срезом (`T-M1-19`). Руководитель разрешается по ПОЛНОМУ
 * списку сотрудников (`include_terminated: true`), а не по тому же
 * списку, что показывает `EmployeesPage` по умолчанию: руководитель может
 * сам быть уволен, но его ФИО в карточке подчинённого всё равно должно
 * разрешиться по имени, а не остаться голым id. */
export function EmployeeCardPage() {
  const { id } = useParams<{ id: string }>();
  const employeeId = Number(id);

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

  return (
    <div>
      <Typography.Title level={3}>{employee.full_name}</Typography.Title>
      <Typography.Text type={employee.employment_status === "terminated" ? "danger" : "success"}>
        {statusText}
      </Typography.Text>

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
    </div>
  );
}
