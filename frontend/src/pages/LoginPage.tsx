import { Button, Card, Form, Input, Typography } from "antd";
import { useNavigate } from "react-router-dom";

import { setAccessToken } from "../app/auth";

interface LoginFormValues {
  email: string;
  password: string;
}

export function LoginPage() {
  const navigate = useNavigate();

  const handleFinish = (values: LoginFormValues) => {
    // Заглушка (T-M0-10): реальный вызов POST /api/auth/login и
    // /api/auth/login/local появится в M1 (FR-AUTH-001/002).
    setAccessToken(`stub-token-for-${values.email}`);
    navigate("/", { replace: true });
  };

  return (
    <div style={{ display: "flex", justifyContent: "center", paddingTop: "10vh" }}>
      <Card title="Система учёта отпусков" style={{ width: 360 }}>
        <Form<LoginFormValues> layout="vertical" onFinish={handleFinish}>
          <Form.Item
            label="Корпоративный e-mail"
            name="email"
            rules={[{ required: true, type: "email" }]}
          >
            <Input autoComplete="username" />
          </Form.Item>
          <Form.Item label="Пароль" name="password" rules={[{ required: true }]}>
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" block>
              Войти
            </Button>
          </Form.Item>
        </Form>
        <Typography.Text type="secondary">
          Вход через LDAP и локальная аутентификация появятся в M1
          (FR-AUTH-001…004).
        </Typography.Text>
      </Card>
    </div>
  );
}
