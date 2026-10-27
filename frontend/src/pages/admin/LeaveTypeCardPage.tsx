import { useQueryClient } from "@tanstack/react-query";
import { Button, Col, Descriptions, Row, Space, Typography } from "antd";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { apiClient } from "../../api/client";
import { EmptyState, ErrorLoadingState, LoadingState } from "../../app/states";
import { LeaveTypeForm } from "../../components/LeaveTypeForm";
import type { LeaveTypeFormSubmitError, LeaveTypeFormValues } from "../../components/LeaveTypeForm";
import { leaveTypeSubmitError } from "../../components/leaveTypeFormError";
import {
  CARRY_OVER_LABELS,
  COUNTED_NON_WORKING_DAYS_LABELS,
  IS_PAID_LABELS,
  LEAVE_TYPE_CARD_BLOCKS,
  UNIT_LABELS,
  YES_NO,
} from "../../entities/leaveType";
import type { LeaveTypeOut } from "../../entities/leaveType";
import { useLeaveTypesQuery } from "../../entities/useLeaveTypesQuery";

/** «Карточка записи» вида отсутствий (`ui.md` §3, `T-M2-14`, `FR-ADMIN-001/1`): просмотр и правка в
 * одном месте (режим правки - переключение содержимого этой страницы, не отдельный маршрут).
 * Отдельного `GET` на один вид в API нет - запись берётся из списка `["leave-types"]`.
 * `PUT` - полная замена: форма отправляет ВСЕ атрибуты, кроме кода (он не меняется). */
export function LeaveTypeCardPage() {
  const { id } = useParams<{ id: string }>();
  const leaveTypeId = Number(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const query = useLeaveTypesQuery();
  const [isEditing, setIsEditing] = useState(false);

  if (query.isLoading) {
    return <LoadingState shape={{ kind: "card", blocks: LEAVE_TYPE_CARD_BLOCKS }} />;
  }
  if (query.isError) {
    return <ErrorLoadingState onRetry={() => query.refetch()} />;
  }

  const leaveType = query.data?.find((candidate) => candidate.id === leaveTypeId);
  if (!leaveType) {
    return (
      <EmptyState
        title="Вид не найден"
        description="Такого вида отсутствий нет в справочнике."
        action={{ label: "К списку видов", onClick: () => navigate("/admin/leave-types") }}
      />
    );
  }

  const handleSubmit = async (
    values: LeaveTypeFormValues,
  ): Promise<LeaveTypeFormSubmitError | null> => {
    // Код не отправляется: сервер отказывает в его смене, а форма показывает его отключённым.
    const { data, error, response } = await apiClient.PUT("/api/leave-types/{leave_type_id}", {
      params: { path: { leave_type_id: leaveType.id } },
      body: {
        name: values.name,
        is_paid: values.is_paid,
        unit: values.unit,
        reduces_main_balance: values.reduces_main_balance,
        balance_tracked: values.balance_tracked,
        requires_document: values.requires_document,
        requires_approval: values.requires_approval,
        annual_limit: values.annual_limit,
        carry_over_policy: values.carry_over_policy,
        counted_non_working_days: values.counted_non_working_days,
        is_active: values.is_active,
      },
    });
    if (error) {
      return leaveTypeSubmitError("PUT", response.status, error.detail, "Не удалось сохранить вид.");
    }
    queryClient.setQueryData<LeaveTypeOut[]>(["leave-types"], (old) =>
      (old ?? []).map((existing) => (existing.id === data.id ? data : existing)),
    );
    await queryClient.invalidateQueries({ queryKey: ["leave-types"] });
    setIsEditing(false);
    return null;
  };

  return (
    <div>
      <Space align="center" style={{ width: "100%", justifyContent: "space-between" }}>
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            {leaveType.name}
          </Typography.Title>
          <Typography.Text type={leaveType.is_active ? "success" : "secondary"}>
            {leaveType.is_active ? "Используется" : "Не используется"} · {leaveType.code}
          </Typography.Text>
        </div>
        {!isEditing && <Button onClick={() => setIsEditing(true)}>Редактировать</Button>}
      </Space>

      {isEditing ? (
        <div style={{ marginTop: 24, maxWidth: 480 }}>
          <LeaveTypeForm mode="edit" initialValues={leaveType} onSubmit={handleSubmit} />
          <Button onClick={() => setIsEditing(false)}>Отмена</Button>
        </div>
      ) : (
        <Row gutter={32} style={{ marginTop: 24 }}>
          <Col span={12}>
            <Descriptions column={1} title="Основное" bordered size="small">
              <Descriptions.Item label="Код">{leaveType.code}</Descriptions.Item>
              <Descriptions.Item label="Название">{leaveType.name}</Descriptions.Item>
              <Descriptions.Item label="Оплачиваемость">{IS_PAID_LABELS[leaveType.is_paid]}</Descriptions.Item>
              <Descriptions.Item label="Единица учёта">{UNIT_LABELS[leaveType.unit]}</Descriptions.Item>
              <Descriptions.Item label="Годовой лимит">{leaveType.annual_limit ?? "—"}</Descriptions.Item>
              <Descriptions.Item label="Перенос неиспользованного остатка">
                {CARRY_OVER_LABELS[leaveType.carry_over_policy]}
              </Descriptions.Item>
            </Descriptions>
          </Col>
          <Col span={12}>
            <Descriptions column={1} title="Правила использования" bordered size="small">
              <Descriptions.Item label="Уменьшает остаток основного отпуска">
                {YES_NO(leaveType.reduces_main_balance)}
              </Descriptions.Item>
              <Descriptions.Item label="По виду ведётся остаток">
                {YES_NO(leaveType.balance_tracked)}
              </Descriptions.Item>
              <Descriptions.Item label="Требует подтверждающий документ">
                {YES_NO(leaveType.requires_document)}
              </Descriptions.Item>
              <Descriptions.Item label="Требует согласования">
                {YES_NO(leaveType.requires_approval)}
              </Descriptions.Item>
              <Descriptions.Item label="Выходные и праздники внутри периода">
                {COUNTED_NON_WORKING_DAYS_LABELS[leaveType.counted_non_working_days]}
              </Descriptions.Item>
              <Descriptions.Item label="Вид используется">{YES_NO(leaveType.is_active)}</Descriptions.Item>
            </Descriptions>
          </Col>
        </Row>
      )}
    </div>
  );
}
