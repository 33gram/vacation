import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Typography } from "antd";
import { useNavigate } from "react-router-dom";

import { apiClient } from "../api/client";
import { fieldError, stringDetail, violationList } from "../api/errors";
import { ErrorLoadingState, LoadingState } from "../app/states";
import { EmployeeForm } from "../components/EmployeeForm";
import type { EmployeeFormSubmitError, EmployeeFormValues } from "../components/EmployeeForm";

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

/** Форма создания (`ui.md` §3, `FR-EMP-001`) — поля и обработка ошибок
 * общие с правкой карточки, см. `EmployeeForm`. */
export function EmployeeCreatePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const departmentsQuery = useDepartmentsQuery();
  const employeesQuery = useAllEmployeesQuery();

  if (departmentsQuery.isLoading || employeesQuery.isLoading) {
    return <LoadingState />;
  }
  if (departmentsQuery.isError) {
    return <ErrorLoadingState onRetry={() => departmentsQuery.refetch()} />;
  }
  if (employeesQuery.isError) {
    return <ErrorLoadingState onRetry={() => employeesQuery.refetch()} />;
  }

  const handleSubmit = async (values: EmployeeFormValues): Promise<EmployeeFormSubmitError | null> => {
    const { data, error } = await apiClient.POST("/api/employees", {
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
        initial_password: values.password,
      },
    });

    if (error) {
      const matchedFieldError = fieldError(error.detail);
      const violations = violationList(error.detail);
      return {
        fieldErrors: matchedFieldError ? [matchedFieldError] : [],
        violatedCodes: violations ? new Set(violations.map((violation) => violation.code)) : null,
        generalError:
          !matchedFieldError && !violations ? (stringDetail(error.detail) ?? "Не удалось создать карточку.") : null,
      };
    }

    await queryClient.invalidateQueries({ queryKey: ["employees"] });
    navigate(`/employees/${data.id}`);
    return null;
  };

  return (
    <div>
      <Typography.Title level={3}>Новый сотрудник</Typography.Title>
      <EmployeeForm
        mode="create"
        departments={departmentsQuery.data ?? []}
        managerOptions={employeesQuery.data ?? []}
        onSubmit={handleSubmit}
      />
    </div>
  );
}
