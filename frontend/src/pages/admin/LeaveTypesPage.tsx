import { Button, Space, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { Link, useNavigate } from "react-router-dom";

import { EmptyState, ErrorLoadingState, LoadingState } from "../../app/states";
import {
  IS_PAID_LABELS,
  LEAVE_TYPES_PAGE_SIZE,
  UNIT_LABELS,
  YES_NO,
} from "../../entities/leaveType";
import type { LeaveTypeOut } from "../../entities/leaveType";
import { useLeaveTypesQuery } from "../../entities/useLeaveTypesQuery";

/** «Администрирование → Виды отсутствий» (`T-M2-14`, `FR-ADMIN-001/1`). Тип экрана по `ui.md` §3:
 * список (эта страница) и «Карточка записи» (`LeaveTypeCardPage`: просмотр и правка - одно место,
 * у вида одиннадцать атрибутов, модальное окно на столько полей читается плохо). Строка
 * фильтров не нужна: в справочнике порядка десятка видов, поэтому её нет. Переход к записи - по
 * первой колонке. Раздел администраторский (`roles.ts`: HR и Админ), хотя `GET /api/leave-types`
 * отдаёт справочник любому аутентифицированному. */
export function LeaveTypesPage() {
  const navigate = useNavigate();
  const query = useLeaveTypesQuery();

  const columns: ColumnsType<LeaveTypeOut> = [
    {
      title: "Код",
      dataIndex: "code",
      key: "code",
      render: (code: string, leaveType) => <Link to={`/admin/leave-types/${leaveType.id}`}>{code}</Link>,
    },
    { title: "Название", dataIndex: "name", key: "name" },
    {
      title: "Оплачиваемость",
      dataIndex: "is_paid",
      key: "is_paid",
      render: (value: LeaveTypeOut["is_paid"]) => IS_PAID_LABELS[value],
    },
    {
      title: "Единица учёта",
      dataIndex: "unit",
      key: "unit",
      render: (value: LeaveTypeOut["unit"]) => UNIT_LABELS[value],
    },
    {
      title: "Ведётся остаток",
      dataIndex: "balance_tracked",
      key: "balance_tracked",
      render: (value: boolean) => YES_NO(value),
    },
    {
      title: "Используется",
      dataIndex: "is_active",
      key: "is_active",
      render: (value: boolean) => YES_NO(value),
    },
  ];

  if (query.isLoading) {
    return (
      <LoadingState shape={{ kind: "table", rows: LEAVE_TYPES_PAGE_SIZE, columns: columns.length }} />
    );
  }
  if (query.isError) {
    return <ErrorLoadingState onRetry={() => query.refetch()} />;
  }

  const leaveTypes = query.data ?? [];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Space align="center" style={{ width: "100%", justifyContent: "space-between" }}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          Виды отсутствий ({leaveTypes.length})
        </Typography.Title>
        <Button type="primary" onClick={() => navigate("/admin/leave-types/new")}>
          Добавить вид
        </Button>
      </Space>

      {leaveTypes.length === 0 ? (
        <EmptyState
          title="Видов отсутствий нет"
          description="В справочнике пока нет ни одного вида отсутствий."
          action={{ label: "Добавить вид", onClick: () => navigate("/admin/leave-types/new") }}
        />
      ) : (
        <Table<LeaveTypeOut>
          rowKey="id"
          columns={columns}
          dataSource={leaveTypes}
          pagination={{ position: ["bottomCenter"], pageSize: LEAVE_TYPES_PAGE_SIZE }}
        />
      )}
    </Space>
  );
}
