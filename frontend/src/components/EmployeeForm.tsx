import { Alert, Button, DatePicker, Form, Input, Radio, Select, Space } from "antd";
import dayjs, { type Dayjs } from "dayjs";
import { useState } from "react";

import type { EmployeeOut } from "../entities/employee";
import type { DepartmentOut } from "../entities/department";
import { PasswordPolicyChecklist } from "./PasswordPolicyChecklist";

type AuthType = "ldap" | "local";
type WorkScheduleType = "standard" | "shift";

/** Значения, которые собирает форма и передаёт наружу — уже в форме,
 * пригодной для тела запроса (`hire_date` как ISO-строка, а не `Dayjs`). */
export interface EmployeeFormValues {
  full_name: string;
  personnel_number: string;
  department_id: number;
  position: string;
  hire_date: string;
  manager_id: number | null;
  email: string;
  work_schedule_type: WorkScheduleType;
  leave_category: string | null;
  auth_type: AuthType;
  /** `initial_password` (создание) или `new_password` (правка) —
   * называть их по-разному наружу незачем, вызывающая сторона знает
   * контекст (`mode`) и раскладывает `password` по нужному полю тела
   * запроса сама. */
  password: string | null;
}

interface FormInternalValues {
  full_name: string;
  personnel_number: string;
  department_id: number;
  position: string;
  hire_date: Dayjs;
  manager_id: number | null | undefined;
  email: string;
  work_schedule_type: WorkScheduleType;
  leave_category: string | null | undefined;
  auth_type: AuthType;
  password: string | undefined;
}

export interface EmployeeFormSubmitError {
  fieldErrors: { field: string; message: string }[];
  violatedCodes: Set<string> | null;
  generalError: string | null;
}

/** Общая форма создания/правки карточки (`ui.md` «Карточка записи» —
 * просмотр и правка в одном месте, `T-M1-19`). Поля — `EmployeeCardFields`
 * + способ входа (`FR-EMP-001`); `PUT` в вызывающем коде обязан отправлять
 * ВЕСЬ этот состав (полная замена, `EmployeeUpdate`), что здесь
 * гарантируется тем, что `handleFinish` собирает значения из ОДНОЙ формы
 * целиком, а не по изменённым полям. */
export function EmployeeForm({
  mode,
  initialValues,
  departments,
  managerOptions,
  onSubmit,
}: {
  mode: "create" | "edit";
  initialValues?: EmployeeOut;
  departments: DepartmentOut[];
  managerOptions: EmployeeOut[];
  onSubmit: (values: EmployeeFormValues) => Promise<EmployeeFormSubmitError | null>;
}) {
  const [form] = Form.useForm<FormInternalValues>();
  const [submitting, setSubmitting] = useState(false);
  const [generalError, setGeneralError] = useState<string | null>(null);
  const [violatedCodes, setViolatedCodes] = useState<Set<string> | null>(null);
  const watchedAuthType = Form.useWatch("auth_type", form);
  const currentAuthType = watchedAuthType ?? initialValues?.auth_type ?? "ldap";

  // Пароль запрашивается при создании локального входа, и при правке —
  // только при ПЕРЕХОДЕ с LDAP на локальный: сервис отклоняет присланный
  // пароль, если способ входа остаётся локальным и без того (T-M1-34,
  // `employee_service._apply_auth_type_change`, третья ветка) — это не
  // смена способа входа, а сброс пароля, вне области этой формы.
  const showsPasswordField =
    currentAuthType === "local" && (mode === "create" || initialValues?.auth_type !== "local");

  const handleFinish = async (values: FormInternalValues) => {
    setGeneralError(null);
    setViolatedCodes(null);
    form.setFields(
      (Object.keys(values) as (keyof FormInternalValues)[]).map((name) => ({ name, errors: [] })),
    );
    setSubmitting(true);
    const error = await onSubmit({
      full_name: values.full_name,
      personnel_number: values.personnel_number,
      department_id: values.department_id,
      position: values.position,
      hire_date: values.hire_date.format("YYYY-MM-DD"),
      manager_id: values.manager_id ?? null,
      email: values.email,
      work_schedule_type: values.work_schedule_type,
      leave_category: values.leave_category ?? null,
      auth_type: values.auth_type,
      password: showsPasswordField ? (values.password ?? null) : null,
    });
    setSubmitting(false);

    if (error) {
      if (error.fieldErrors.length > 0) {
        form.setFields(
          error.fieldErrors.map((fieldErrorItem) => ({
            name: fieldErrorItem.field as keyof FormInternalValues,
            errors: [fieldErrorItem.message],
          })),
        );
      }
      if (error.violatedCodes) {
        setViolatedCodes(error.violatedCodes);
      }
      if (error.generalError) {
        setGeneralError(error.generalError);
      }
    }
  };

  return (
    <Form<FormInternalValues>
      form={form}
      layout="vertical"
      onFinish={handleFinish}
      initialValues={
        initialValues
          ? {
              full_name: initialValues.full_name,
              personnel_number: initialValues.personnel_number,
              department_id: initialValues.department_id ?? undefined,
              position: initialValues.position,
              hire_date: dayjs(initialValues.hire_date),
              manager_id: initialValues.manager_id,
              email: initialValues.email,
              work_schedule_type: initialValues.work_schedule_type as WorkScheduleType,
              leave_category: initialValues.leave_category,
              auth_type: initialValues.auth_type,
            }
          : { auth_type: "ldap", work_schedule_type: "standard" }
      }
    >
      <Form.Item label="ФИО" name="full_name" rules={[{ required: true }]}>
        <Input />
      </Form.Item>
      <Form.Item label="Табельный номер" name="personnel_number" rules={[{ required: true }]}>
        <Input />
      </Form.Item>
      <Form.Item label="Подразделение" name="department_id" rules={[{ required: true }]}>
        <Select
          showSearch
          optionFilterProp="label"
          options={departments.map((department) => ({ value: department.id, label: department.name }))}
        />
      </Form.Item>
      <Form.Item label="Должность" name="position" rules={[{ required: true }]}>
        <Input />
      </Form.Item>
      <Form.Item label="Дата приёма" name="hire_date" rules={[{ required: true }]}>
        <DatePicker format="DD.MM.YYYY" style={{ width: "100%" }} />
      </Form.Item>
      <Form.Item label="Руководитель" name="manager_id">
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="Нет (вершина иерархии)"
          options={managerOptions
            .filter((employee) => employee.id !== initialValues?.id)
            .map((employee) => ({ value: employee.id, label: employee.full_name }))}
        />
      </Form.Item>
      <Form.Item label="E-mail" name="email" rules={[{ required: true, type: "email" }]}>
        <Input />
      </Form.Item>
      <Form.Item label="График работы" name="work_schedule_type" rules={[{ required: true }]}>
        <Radio.Group
          options={[
            { value: "standard", label: "Стандартный" },
            { value: "shift", label: "Сменный" },
          ]}
        />
      </Form.Item>
      <Form.Item label="Категория для расчёта" name="leave_category">
        <Input />
      </Form.Item>
      <Form.Item label="Способ входа" name="auth_type" rules={[{ required: true }]}>
        <Radio.Group
          options={[
            { value: "ldap", label: "Корпоративный каталог (LDAP)" },
            { value: "local", label: "Локальный пароль" },
          ]}
        />
      </Form.Item>

      {showsPasswordField && (
        <>
          <PasswordPolicyChecklist violatedCodes={violatedCodes} />
          <Form.Item
            label={mode === "create" ? "Первичный пароль" : "Новый пароль"}
            name="password"
            rules={[{ required: true }]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </>
      )}

      {generalError && (
        <Alert type="error" message={generalError} showIcon style={{ marginBottom: 16 }} />
      )}

      <Form.Item>
        <Space>
          <Button type="primary" htmlType="submit" loading={submitting}>
            {mode === "create" ? "Создать" : "Сохранить"}
          </Button>
        </Space>
      </Form.Item>
    </Form>
  );
}
