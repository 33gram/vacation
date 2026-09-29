import { Alert, Button, Form, Modal, Select, Space } from "antd";
import { useState } from "react";

import type { EmployeeOut } from "../entities/employee";

/** Назначение руководителя подразделения (`T-M1-20`, коммит 3) — поиск
 * по списку, а не перетаскивание (решение владельца продукта: drag-and-
 * drop в этом разделе переносит узел дерева, назначение руководителя —
 * отдельное действие).
 *
 * `employeeOptions` — только АКТИВНЫЕ сотрудники: сервер отклоняет
 * уволенного руководителем (`department_service._require_active_head_
 * reference`), и предлагать в списке заведомо отклоняемый вариант —
 * ловушка для пользователя, который увидит отказ только после отправки
 * формы. Это не то же самое, что разрешение ФИО в самом дереве
 * (`DepartmentsPage`, коммит 1) — там нужен ПОЛНЫЙ список, включая
 * уволенных, иначе уже назначенный (и позже уволенный) руководитель
 * отображался бы прочерком. */
export function DepartmentHeadModal({
  open,
  departmentName,
  currentHeadId,
  employeeOptions,
  onCancel,
  onSubmit,
}: {
  open: boolean;
  departmentName: string;
  currentHeadId: number | null;
  employeeOptions: EmployeeOut[];
  onCancel: () => void;
  onSubmit: (headEmployeeId: number | null) => Promise<{ field: string; message: string } | string | null>;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [generalError, setGeneralError] = useState<string | null>(null);

  const handleFinish = async (values: { head_employee_id: number | null | undefined }) => {
    setSubmitting(true);
    setFieldError(null);
    setGeneralError(null);
    const result = await onSubmit(values.head_employee_id ?? null);
    setSubmitting(false);
    if (result === null) {
      return;
    }
    if (typeof result === "string") {
      setGeneralError(result);
      return;
    }
    setFieldError(result.message);
  };

  return (
    <Modal
      title={`Назначить руководителя: ${departmentName}`}
      open={open}
      onCancel={onCancel}
      footer={null}
      destroyOnHidden
    >
      <Form
        layout="vertical"
        onFinish={handleFinish}
        initialValues={{ head_employee_id: currentHeadId }}
      >
        <Form.Item
          label="Руководитель"
          name="head_employee_id"
          validateStatus={fieldError ? "error" : undefined}
          help={fieldError ?? undefined}
        >
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder="Нет"
            options={employeeOptions.map((employee) => ({
              value: employee.id,
              label: employee.full_name,
            }))}
          />
        </Form.Item>
        {generalError && (
          <Alert type="error" message={generalError} showIcon style={{ marginBottom: 16 }} />
        )}
        <Form.Item style={{ marginBottom: 0 }}>
          <Space>
            <Button type="primary" htmlType="submit" loading={submitting}>
              Назначить
            </Button>
            <Button onClick={onCancel}>Отмена</Button>
          </Space>
        </Form.Item>
      </Form>
    </Modal>
  );
}
