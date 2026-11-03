import { Alert, Button, Card, Form, Input } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { formValidation, stringDetail } from "../api/errors";
import { apiClient } from "../api/client";
import { setAccessToken } from "../app/auth";

interface LoginFormValues {
  login: string;
  password: string;
}

// FR-AUTH-001/003, T-M1-18: единственный вызов — `POST /api/auth/login`.
// Диспетчеризацию LDAP/локальный вход делает backend (`local_service.
// uses_local_auth`, T-M1-32) — клиент про способ входа не знает и выбор не
// показывает.
//
// `POST /api/auth/login/local` интерфейсу не нужен вовсе (проверено по
// коду `router.py`/`local_service.py`, а не оставлено «на всякий случай»):
// диспетчер `/api/auth/login` уходит в `authenticate_local` по условию
// `is_embedded_mode(...) OR uses_local_auth(...)` — вторая часть условия
// одна уже покрывает ЛЮБУЮ учётную запись с `auth_type = 'local'`,
// независимо от режима внедрения: и bootstrap-администратора без карточки
// (FR-AUTH-005), и сотрудника с явно назначенным локальным паролем
// (FR-EMP-001). Отдельный маршрут для локального входа не остаётся
// клиенту не покрытым ни одним реальным случаем — сценария, где `/login`
// увёл бы такую запись в LDAP, в коде диспетчера нет.
export function LoginPage() {
  const navigate = useNavigate();
  const [form] = Form.useForm<LoginFormValues>();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleFinish = async (values: LoginFormValues) => {
    setErrorMessage(null);
    form.setFields([
      { name: "login", errors: [] },
      { name: "password", errors: [] },
    ]);
    setSubmitting(true);
    const { data, error } = await apiClient.POST("/api/auth/login", {
      body: { login: values.login, password: values.password },
    });
    setSubmitting(false);

    if (error) {
      // ui.md §7: текст ошибки от сервера показывается как есть, уже
      // по-русски («Неверный логин или пароль», «Доступ запрещён.
      // Обратитесь к HR-администратору.») — не переписывается на клиенте.
      // 422 (`T-M2-28`): ошибки полей - под полями, остальное - общим текстом.
      const validation = formValidation(error.detail, ["login", "password"]);
      if (validation) {
        form.setFields(
          validation.fieldErrors.map(({ field, message }) => ({ name: field, errors: [message] })),
        );
        setErrorMessage(validation.generalError);
        return;
      }
      setErrorMessage(stringDetail(error.detail) ?? "Не удалось войти. Попробуйте ещё раз.");
      return;
    }

    setAccessToken(data.access_token);
    navigate("/", { replace: true });
  };

  return (
    <div style={{ display: "flex", justifyContent: "center", paddingTop: "10vh" }}>
      <Card title="Система учёта отпусков" style={{ width: 360 }}>
        {errorMessage && (
          <Alert
            type="error"
            message={errorMessage}
            showIcon
            style={{ marginBottom: 16 }}
          />
        )}
        <Form<LoginFormValues> form={form} layout="vertical" onFinish={handleFinish}>
          <Form.Item
            label="Логин или e-mail"
            name="login"
            rules={[{ required: true }]}
          >
            {/* ui.md §7: поле принимает и служебный логин без карточки
                сотрудника (bootstrap-администратор, FR-AUTH-005), и
                корпоративный e-mail — `type="email"` здесь отклонил бы
                обычный логин, формат проверяет сервер, не форма
                (PR #144 review). */}
            <Input autoComplete="username" placeholder="Служебный логин или корпоративный e-mail" />
          </Form.Item>
          <Form.Item label="Пароль" name="password" rules={[{ required: true }]}>
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" block loading={submitting}>
              Войти
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
