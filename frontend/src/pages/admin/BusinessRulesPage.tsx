import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Col, Form, Input, Row, Space, Typography } from "antd";
import { useState } from "react";

import { apiClient } from "../../api/client";
import { formValidation, stringDetail } from "../../api/errors";
import { formatDateTime } from "../../app/format";
import { EmptyState, ErrorLoadingState, LoadingState } from "../../app/states";
import {
  BUSINESS_RULES_FIELD_COUNT,
  changedPairs,
  checkBeforeSend,
  inputKindOf,
  rangeHint,
  rulesErrorsByKey,
  valuesOf,
} from "../../entities/businessRules";
import type { BusinessRuleOut } from "../../entities/businessRules";
import { BUSINESS_RULES_QUERY_KEY, useBusinessRulesQuery } from "../../entities/useBusinessRulesQuery";

const SAVE_FAILED = "Не удалось сохранить параметры.";

/** Кто и когда менял параметр: ответ `GET` отдаёт оба поля. `updated_by_name = null` - автор не
 * указан (значение засеяно миграцией); аудит правок с полной историей - `T-M6-02`. */
function changedBy(rule: BusinessRuleOut): string {
  const when = formatDateTime(rule.updated_at);
  return rule.updated_by_name
    ? `Изменён: ${when}, ${rule.updated_by_name}`
    : `Обновлён: ${when}, автор не указан`;
}

function BusinessRulesForm({ rules }: { rules: readonly BusinessRuleOut[] }) {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<Record<string, string>>();
  const [generalError, setGeneralError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState(false);
  const typed = Form.useWatch([], form) as Record<string, string> | undefined;
  const pairs = changedPairs(rules, typed ?? valuesOf(rules));
  const hasChanges = Object.keys(pairs).length > 0;

  const saveMutation = useMutation({
    mutationFn: async (values: Record<string, string>) => {
      const { data, error, response } = await apiClient.PUT("/api/admin/business-rules", {
        body: { values },
      });
      if (error) {
        return { rules: null, status: response.status, detail: error.detail };
      }
      return { rules: data, status: response.status, detail: null };
    },
    onSuccess: async (result) => {
      if (result.rules === null) {
        // 400 несёт ВСЕ негодные ключи строкой `ключ: текст; ключ: текст` - каждый под своё поле.
        // 422 - ошибки тела: каждая под своё поле по последнему элементу `loc` (`T-M2-28`, ключ
        // параметра), остальное - общим текстом. Не в этих формах - общим текстом сервера.
        // 404 (неизвестный ключ) из формы недостижим: ключи пришли из `GET`; если он всё же
        // пришёл (реестр изменился под открытой формой) - текст сервера общей ошибкой, а форма
        // и набранное остаются, чтобы правки не терялись.
        // 400 называет только ПРИСЛАННЫЕ ключи, а присылаются ключи из `GET` (неизвестный даёт 404):
        // разбор по полям не промахивается, служебный ключ в общем тексте не появляется.
        const byKey = result.status === 400 ? rulesErrorsByKey(result.detail, rules.map((r) => r.key)) : null;
        const validation =
          result.status === 422
            ? formValidation(result.detail, rules.map((r) => r.key))
            : null;
        if (byKey) {
          setGeneralError(null);
          form.setFields(Object.entries(byKey).map(([name, message]) => ({ name, errors: [message] })));
        } else if (validation) {
          setGeneralError(validation.generalError);
          form.setFields(
            validation.fieldErrors.map(({ field, message }) => ({ name: field, errors: [message] })),
          );
        } else {
          setGeneralError(stringDetail(result.detail) ?? SAVE_FAILED);
        }
        return;
      }
      setGeneralError(null);
      setSavedNotice(true);
      // Сервер хранит КАНОНИЧЕСКУЮ форму («029» -> «29») и отвечает реестром целиком: и кэш, и
      // поля берутся из ответа, не из набранного - иначе поле и база разошлись бы на глазах.
      queryClient.setQueryData(BUSINESS_RULES_QUERY_KEY, result.rules);
      form.setFieldsValue(valuesOf(result.rules));
      // Минимальная длина пароля (`password_min_length`) входит в подсказки политики пароля.
      await queryClient.invalidateQueries({ queryKey: ["password-policy"] });
    },
    onError: () => {
      setGeneralError(SAVE_FAILED);
    },
  });

  const handleFinish = (values: Record<string, string>) => {
    setSavedNotice(false);
    // Только изменённые пары: сервер принимает набор, проверяет целиком, а пару, равную текущему
    // значению, не пишет - но и не нужно отправлять то, чего не меняли.
    saveMutation.mutate(changedPairs(rules, values));
  };

  return (
    <Form<Record<string, string>>
      form={form}
      layout="vertical"
      initialValues={valuesOf(rules)}
      onFinish={handleFinish}
      onValuesChange={() => setSavedNotice(false)}
    >
      <Row gutter={32}>
        {rules.map((rule) => {
          const hint = rangeHint(rule);
          return (
            <Col span={12} key={rule.key}>
              <Form.Item
                label={rule.description}
                name={rule.key}
                rules={[
                  {
                    validator: async (_, value: string | undefined) => {
                      const problem = checkBeforeSend(rule, value ?? "");
                      if (problem) {
                        throw new Error(problem);
                      }
                    },
                  },
                ]}
                extra={
                  <>
                    <div>
                      <code>{rule.key}</code>
                    </div>
                    {hint && <div>{hint}</div>}
                    <div>{changedBy(rule)}</div>
                  </>
                }
              >
                <Input inputMode={inputKindOf(rule) === "integer" ? "numeric" : undefined} />
              </Form.Item>
            </Col>
          );
        })}
      </Row>

      {generalError && <Alert type="error" message={generalError} showIcon style={{ marginBottom: 16 }} />}
      {savedNotice && <Alert type="success" message="Параметры сохранены." showIcon style={{ marginBottom: 16 }} />}

      <Form.Item>
        <Space>
          <Button type="primary" htmlType="submit" disabled={!hasChanges} loading={saveMutation.isPending}>
            Сохранить
          </Button>
        </Space>
      </Form.Item>
    </Form>
  );
}

/** «Администрирование → Параметры бизнес-правил» (`T-M2-15`, `FR-ADMIN-004/1`). Тип экрана по
 * `ui.md` §3 - «Форма настроек»: поля в две колонки (в порядке ответа `GET`, слева направо), пояснение
 * под полем, действие внизу слева; ложится без пятого типа. Источник - только
 * `GET/PUT /api/admin/business-rules`; описание, границы, автор и время приходят из ответа, своих
 * текстов нет. Подпись поля - `description` из ответа (он русский): интерфейс на русском языке
 * (`NFR-LOC-001`, `ui.md` §6), решение заказчика; подпись связана с полем (`label`, `ui.md` §9).
 * Ключ параметра показан второй строкой ПОД полем (`<code>`): он нужен поддержке и совпадает с
 * ключом в ответах сервера об ошибке. `description` стоит только в подписи, под полем его нет.
 * Доступ - HR и Админ (`roles.ts`, строка матрицы раздела 7). */
export function BusinessRulesPage() {
  const query = useBusinessRulesQuery();

  if (query.isLoading) {
    // Число полей равно числу ключей реестра и от данных не зависит: константа охраняется
    // backend-тестом `test_business_rules_skeleton_count.py` (сверка с `len(RULES)`), как число у
    // `PasswordPolicyChecklist`. С отрисованной формой (две колонки, подписи) она не сверяется.
    return <LoadingState shape={{ kind: "form", fields: BUSINESS_RULES_FIELD_COUNT }} />;
  }
  if (query.isError) {
    return <ErrorLoadingState onRetry={() => query.refetch()} />;
  }

  const rules = query.data ?? [];

  return (
    <div>
      <Typography.Title level={3}>Администрирование → Параметры бизнес-правил</Typography.Title>
      {rules.length === 0 ? (
        <EmptyState title="Параметров нет" description="Реестр параметров бизнес-правил пуст." />
      ) : (
        <BusinessRulesForm rules={rules} />
      )}
    </div>
  );
}
