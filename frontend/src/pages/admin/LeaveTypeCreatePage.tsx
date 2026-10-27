import { useQueryClient } from "@tanstack/react-query";
import { Typography } from "antd";
import { useNavigate } from "react-router-dom";

import { apiClient } from "../../api/client";
import { LeaveTypeForm } from "../../components/LeaveTypeForm";
import type { LeaveTypeFormSubmitError, LeaveTypeFormValues } from "../../components/LeaveTypeForm";
import { leaveTypeSubmitError } from "../../components/leaveTypeFormError";

/** «Новый вид отсутствий» (`T-M2-14`, `FR-ADMIN-001/1`): форма со ВСЕМИ атрибутами раздела 6. */
export function LeaveTypeCreatePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const handleSubmit = async (
    values: LeaveTypeFormValues,
  ): Promise<LeaveTypeFormSubmitError | null> => {
    const { data, error, response } = await apiClient.POST("/api/leave-types", { body: values });
    if (error) {
      return leaveTypeSubmitError("POST", response.status, error.detail, "Не удалось создать вид.");
    }
    await queryClient.invalidateQueries({ queryKey: ["leave-types"] });
    navigate(`/admin/leave-types/${data.id}`);
    return null;
  };

  return (
    <div style={{ maxWidth: 480 }}>
      <Typography.Title level={3}>Новый вид отсутствий</Typography.Title>
      <LeaveTypeForm mode="create" onSubmit={handleSubmit} />
    </div>
  );
}
