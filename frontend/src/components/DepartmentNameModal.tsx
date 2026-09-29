import { Alert, Button, Form, Input, Modal, Space } from "antd";
import { useState } from "react";

/** Модальное окно с единственным полем «Название» — общее для создания
 * подразделения (`T-M1-20`, коммит 2) и переименования: в обоих случаях
 * форма собирает только имя, а `parent_id`/`head_employee_id` (для
 * переименования) вызывающая сторона берёт из уже прочитанного
 * `DepartmentOut`, а не из этой формы (см. `DepartmentsPage` — полная
 * замена `PUT`, все три поля). `destroyOnHidden` — форма размонтируется
 * при закрытии, следующее открытие получает чистое состояние из
 * `initialName` без ручного сброса полей. */
export function DepartmentNameModal({
  open,
  title,
  submitLabel,
  initialName,
  onCancel,
  onSubmit,
}: {
  open: boolean;
  title: string;
  submitLabel: string;
  initialName?: string;
  onCancel: () => void;
  onSubmit: (name: string) => Promise<string | null>;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleFinish = async (values: { name: string }) => {
    setSubmitting(true);
    setError(null);
    const message = await onSubmit(values.name);
    setSubmitting(false);
    if (message) {
      setError(message);
    }
  };

  return (
    <Modal title={title} open={open} onCancel={onCancel} footer={null} destroyOnHidden>
      <Form layout="vertical" onFinish={handleFinish} initialValues={{ name: initialName ?? "" }}>
        <Form.Item label="Название" name="name" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 16 }} />}
        <Form.Item style={{ marginBottom: 0 }}>
          <Space>
            <Button type="primary" htmlType="submit" loading={submitting}>
              {submitLabel}
            </Button>
            <Button onClick={onCancel}>Отмена</Button>
          </Space>
        </Form.Item>
      </Form>
    </Modal>
  );
}
