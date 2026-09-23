import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Form, Input, Layout, List, Typography } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { apiClient } from "../api/client";
import { stringDetail, violationList } from "../api/errors";
import { clearAccessToken } from "../app/auth";
import { ErrorLoadingState, LoadingState } from "../app/states";

const { Header, Content } = Layout;

interface ChangePasswordFormValues {
  current_password: string;
  new_password: string;
}

/** FR-AUTH-002, T-M1-18: чек-лист правил строится из `GET /api/auth/
 * password-policy` ([{code, hint}], T-M1-45) — клиент не вычисляет
 * выполнение правил сам и не хардкодит ни одного параметра политики
 * (минимальная длина — бизнес-правило, T-M2-09). После отказа 400
 * красным подсвечиваются ровно те пункты, чьи `code` пришли в теле
 * ответа (`detail` — список `{code, message}`) — все сразу, а не первый
 * (`ui.md` §7, FR-AUTH-002).
 *
 * Экран стоит ВНЕ оболочки (`AppShell`) и рендерится, пока `GET /api/auth/
 * me` сообщает `must_change_password = true` (`SessionGate`) — единственный
 * выход отсюда, кроме самой смены пароля, — «Выйти» в шапке. */
export function ChangePasswordPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [violatedCodes, setViolatedCodes] = useState<Set<string> | null>(null);
  const [generalError, setGeneralError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const policyQuery = useQuery({
    queryKey: ["password-policy"],
    queryFn: async () => {
      const { data, response } = await apiClient.GET("/api/auth/password-policy");
      if (!data) {
        throw new Error(`GET /api/auth/password-policy failed with status ${response.status}`);
      }
      return data;
    },
    retry: false,
  });

  const handleLogout = () => {
    clearAccessToken();
    navigate("/login", { replace: true });
  };

  const handleFinish = async (values: ChangePasswordFormValues) => {
    setGeneralError(null);
    setViolatedCodes(null);
    setSubmitting(true);
    const { error } = await apiClient.POST("/api/auth/password/change", {
      body: values,
    });
    setSubmitting(false);

    if (error) {
      const violations = violationList(error.detail);
      if (violations) {
        setViolatedCodes(new Set(violations.map((violation) => violation.code)));
      } else {
        // T-M1-47 ещё не сделана: "Invalid current password" (401) и "Only
        // local accounts have a password to change" (400) временно
        // приходят по-английски. Показываются как есть — переписывать
        // серверный текст на клиенте запрещено (ui.md §7); свой перевод
        // здесь не заводится.
        setGeneralError(stringDetail(error.detail) ?? "Не удалось изменить пароль.");
      }
      return;
    }

    // Успех (204) — источник истины о том, что пароль сменён, это /me, а
    // не факт успешного ответа: перечитываем его, `SessionGate` пропустит
    // дальше сам.
    await queryClient.invalidateQueries({ queryKey: ["me"] });
  };

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <Typography.Title level={4} style={{ color: "#fff", margin: 0 }}>
          Смена пароля
        </Typography.Title>
        <Button onClick={handleLogout}>Выйти</Button>
      </Header>
      <Content style={{ padding: 24, maxWidth: 480, margin: "0 auto", width: "100%" }}>
        <Typography.Paragraph>
          Перед первым входом в систему необходимо задать новый пароль.
        </Typography.Paragraph>

        {policyQuery.isLoading && <LoadingState />}
        {policyQuery.isError && (
          <ErrorLoadingState onRetry={() => policyQuery.refetch()} />
        )}
        {policyQuery.data && (
          <List
            size="small"
            header={<Typography.Text strong>Требования к паролю</Typography.Text>}
            dataSource={policyQuery.data}
            style={{ marginBottom: 24 }}
            renderItem={(hint) => {
              const violated = violatedCodes?.has(hint.code) ?? false;
              return (
                <List.Item>
                  {/* Нарушение отмечается и текстом, не только цветом
                      (type="danger") — красный сам по себе не годится ни
                      для доступности (ui.md §9), ни для проверки в тестах
                      "по подписи, не по структуре разметки". */}
                  <Typography.Text type={violated ? "danger" : undefined}>
                    {violated ? `${hint.hint} — не выполнено` : hint.hint}
                  </Typography.Text>
                </List.Item>
              );
            }}
          />
        )}

        {generalError && (
          <Alert type="error" message={generalError} showIcon style={{ marginBottom: 16 }} />
        )}

        <Form<ChangePasswordFormValues> layout="vertical" onFinish={handleFinish}>
          <Form.Item
            label="Текущий пароль"
            name="current_password"
            rules={[{ required: true }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item label="Новый пароль" name="new_password" rules={[{ required: true }]}>
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" block loading={submitting}>
              Сменить пароль
            </Button>
          </Form.Item>
        </Form>
      </Content>
    </Layout>
  );
}
