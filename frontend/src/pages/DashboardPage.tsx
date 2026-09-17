import { Button, Layout, Typography } from "antd";
import { useNavigate } from "react-router-dom";

import { clearAccessToken } from "../app/auth";

const { Header, Content } = Layout;

export function DashboardPage() {
  const navigate = useNavigate();

  const handleLogout = () => {
    clearAccessToken();
    navigate("/login", { replace: true });
  };

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <Typography.Title level={4} style={{ color: "#fff", margin: 0 }}>
          Система учёта отпусков
        </Typography.Title>
        <Button onClick={handleLogout}>Выйти</Button>
      </Header>
      <Content style={{ padding: 24 }}>
        <Typography.Paragraph>
          Каркас проекта (M0). Разделы «Мой отпуск», «Заявки», «Календарь
          команды» и «Администрирование» появятся по мере реализации этапов
          M1–M6.
        </Typography.Paragraph>
      </Content>
    </Layout>
  );
}
