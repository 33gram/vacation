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
  FIELD_LABELS,
  IS_PAID_LABELS,
  LEAVE_TYPE_CARD_BLOCKS,
  UNIT_LABELS,
  yesNo,
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
    // Код не отправляется: сервер отказывает в его смене, форма показывает его отключённым.
    // eslint-disable-next-line @typescript-eslint/no-unused-vars -- `code` выделяется ради `rest`
    const { code, ...rest } = values;
    const { data, error, response } = await apiClient.PUT("/api/leave-types/{leave_type_id}", {
      params: { path: { leave_type_id: leaveType.id } },
      body: rest,
    });
    if (error) {
      return leaveTypeSubmitError("PUT", response.status, error.detail, "Не удалось сохранить вид.");
    }
    // Оба нужны: `setQueryData` показывает сохранённую запись сразу (карточка выходит из правки
    // без мигания прежних значений), `invalidateQueries` затем подтверждает состояние отдельным
    // `GET` - подтверждённое состояние должно прийти с сервера, а не только из тела ответа `PUT`.
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
              <Descriptions.Item label={FIELD_LABELS.code}>{leaveType.code}</Descriptions.Item>
              <Descriptions.Item label={FIELD_LABELS.name}>{leaveType.name}</Descriptions.Item>
              <Descriptions.Item label={FIELD_LABELS.is_paid}>{IS_PAID_LABELS[leaveType.is_paid]}</Descriptions.Item>
              <Descriptions.Item label={FIELD_LABELS.unit}>{UNIT_LABELS[leaveType.unit]}</Descriptions.Item>
              <Descriptions.Item label={FIELD_LABELS.annual_limit}>{leaveType.annual_limit ?? "—"}</Descriptions.Item>
              <Descriptions.Item label={FIELD_LABELS.carry_over_policy}>
                {CARRY_OVER_LABELS[leaveType.carry_over_policy]}
              </Descriptions.Item>
            </Descriptions>
          </Col>
          <Col span={12}>
            <Descriptions column={1} title="Правила использования" bordered size="small">
              <Descriptions.Item label={FIELD_LABELS.reduces_main_balance}>
                {yesNo(leaveType.reduces_main_balance)}
              </Descriptions.Item>
              <Descriptions.Item label={FIELD_LABELS.balance_tracked}>
                {yesNo(leaveType.balance_tracked)}
              </Descriptions.Item>
              <Descriptions.Item label={FIELD_LABELS.requires_document}>
                {yesNo(leaveType.requires_document)}
              </Descriptions.Item>
              <Descriptions.Item label={FIELD_LABELS.requires_approval}>
                {yesNo(leaveType.requires_approval)}
              </Descriptions.Item>
              <Descriptions.Item label={FIELD_LABELS.counted_non_working_days}>
                {COUNTED_NON_WORKING_DAYS_LABELS[leaveType.counted_non_working_days]}
              </Descriptions.Item>
              <Descriptions.Item label={FIELD_LABELS.is_active}>{yesNo(leaveType.is_active)}</Descriptions.Item>
            </Descriptions>
          </Col>
        </Row>
      )}
    </div>
  );
}
