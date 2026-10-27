import { Alert, Button, Form, Input, InputNumber, Select, Switch } from "antd";
import { useState } from "react";

import {
  CARRY_OVER_LABELS,
  COUNTED_NON_WORKING_DAYS_LABELS,
  FIELD_LABELS,
  IS_PAID_LABELS,
  UNIT_LABELS,
  optionsOf,
} from "../entities/leaveType";
import type {
  CarryOverPolicy,
  CountedNonWorkingDays,
  IsPaid,
  LeaveTypeOut,
  Unit,
} from "../entities/leaveType";

export interface LeaveTypeFormValues {
  code: string;
  name: string;
  is_paid: IsPaid;
  unit: Unit;
  reduces_main_balance: boolean;
  balance_tracked: boolean;
  requires_document: boolean;
  requires_approval: boolean;
  annual_limit: string | null;
  carry_over_policy: CarryOverPolicy;
  counted_non_working_days: CountedNonWorkingDays;
  is_active: boolean;
}

export interface LeaveTypeFormSubmitError {
  fieldErrors: { field: keyof LeaveTypeFormValues; message: string }[];
  generalError: string | null;
}

/** Форма вида отсутствий: ВСЕ атрибуты раздела 6 требований (`FR-ADMIN-001/1`). Код задаётся
 * при создании и не меняется при правке (сервер отказывает в смене, `LeaveTypeCodeImmutableError`)
 * - в режиме правки он показан отключённым полем и в `onSubmit` не используется. Отказы сервера
 * приходят как `LeaveTypeFormSubmitError`: у вызывающего кода, который знает, какой запрос
 * отказал, - по полю или общим текстом над кнопками (`ui.md` §7). */
export function LeaveTypeForm({
  mode,
  initialValues,
  onSubmit,
}: {
  mode: "create" | "edit";
  initialValues?: LeaveTypeOut;
  onSubmit: (values: LeaveTypeFormValues) => Promise<LeaveTypeFormSubmitError | null>;
}) {
  const [form] = Form.useForm<LeaveTypeFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [generalError, setGeneralError] = useState<string | null>(null);

  const handleFinish = async (values: LeaveTypeFormValues) => {
    setGeneralError(null);
    form.setFields(
      (Object.keys(values) as (keyof LeaveTypeFormValues)[]).map((name) => ({ name, errors: [] })),
    );
    setSubmitting(true);
    const error = await onSubmit({
      ...values,
      annual_limit: values.annual_limit ?? null,
    });
    setSubmitting(false);

    if (error) {
      if (error.fieldErrors.length > 0) {
        form.setFields(
          error.fieldErrors.map((item) => ({ name: item.field, errors: [item.message] })),
        );
      }
      if (error.generalError) {
        setGeneralError(error.generalError);
      }
    }
  };

  return (
    <Form<LeaveTypeFormValues>
      form={form}
      layout="vertical"
      onFinish={handleFinish}
      initialValues={
        initialValues
          ? {
              code: initialValues.code,
              name: initialValues.name,
              is_paid: initialValues.is_paid,
              unit: initialValues.unit,
              reduces_main_balance: initialValues.reduces_main_balance,
              balance_tracked: initialValues.balance_tracked,
              requires_document: initialValues.requires_document,
              requires_approval: initialValues.requires_approval,
              annual_limit: initialValues.annual_limit ?? null,
              carry_over_policy: initialValues.carry_over_policy,
              counted_non_working_days: initialValues.counted_non_working_days,
              is_active: initialValues.is_active,
            }
          : {
              is_paid: "full",
              unit: "calendar_days",
              reduces_main_balance: false,
              balance_tracked: false,
              requires_document: false,
              requires_approval: true,
              annual_limit: null,
              carry_over_policy: "none",
              counted_non_working_days: "all",
              is_active: true,
            }
      }
    >
      <Form.Item
        label={FIELD_LABELS.code}
        name="code"
        rules={[{ required: true }]}
        extra={mode === "edit" ? "Код вида не меняется." : "Латиницей, строчными буквами, цифрами и «_»."}
      >
        <Input disabled={mode === "edit"} />
      </Form.Item>
      <Form.Item label={FIELD_LABELS.name} name="name" rules={[{ required: true }]}>
        <Input />
      </Form.Item>
      <Form.Item label={FIELD_LABELS.is_paid} name="is_paid" rules={[{ required: true }]}>
        <Select options={optionsOf(IS_PAID_LABELS)} />
      </Form.Item>
      <Form.Item label={FIELD_LABELS.unit} name="unit" rules={[{ required: true }]}>
        <Select options={optionsOf(UNIT_LABELS)} />
      </Form.Item>
      <Form.Item
        label={FIELD_LABELS.reduces_main_balance}
        name="reduces_main_balance"
        valuePropName="checked"
      >
        <Switch />
      </Form.Item>
      <Form.Item
        label={FIELD_LABELS.balance_tracked}
        name="balance_tracked"
        valuePropName="checked"
        extra="Система ведёт остаток по этому виду и показывает его в разделе «Мой отпуск»."
      >
        <Switch />
      </Form.Item>
      <Form.Item label={FIELD_LABELS.requires_document} name="requires_document" valuePropName="checked">
        <Switch />
      </Form.Item>
      <Form.Item label={FIELD_LABELS.requires_approval} name="requires_approval" valuePropName="checked">
        <Switch />
      </Form.Item>
      <Form.Item
        label={FIELD_LABELS.annual_limit}
        name="annual_limit"
        extra="Число в год; пусто, если лимита нет."
      >
        <InputNumber<string> stringMode min="0" step="0.5" style={{ width: "100%" }} />
      </Form.Item>
      <Form.Item label={FIELD_LABELS.carry_over_policy} name="carry_over_policy" rules={[{ required: true }]}>
        <Select options={optionsOf(CARRY_OVER_LABELS)} />
      </Form.Item>
      <Form.Item
        label={FIELD_LABELS.counted_non_working_days}
        name="counted_non_working_days"
        rules={[{ required: true }]}
      >
        <Select options={optionsOf(COUNTED_NON_WORKING_DAYS_LABELS)} />
      </Form.Item>
      <Form.Item label={FIELD_LABELS.is_active} name="is_active" valuePropName="checked">
        <Switch />
      </Form.Item>

      {generalError && (
        <Alert type="error" message={generalError} showIcon style={{ marginBottom: 16 }} />
      )}

      <Form.Item>
        <Button type="primary" htmlType="submit" loading={submitting}>
          {mode === "create" ? "Создать" : "Сохранить"}
        </Button>
      </Form.Item>
    </Form>
  );
}
