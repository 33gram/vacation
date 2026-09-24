import { Breadcrumb, Button, Layout, Menu, Typography } from "antd";
import type { MenuProps } from "antd";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { EmbeddedModeBanner } from "./EmbeddedModeBanner";
import { menuItemsForRole, roleLabel, type MenuItemConfig, asRole } from "./roles";
import { useSession } from "./SessionContext";
import { useLogout } from "./useLogout";

const { Header, Sider, Content } = Layout;

function buildMenuItems(items: MenuItemConfig[]): MenuProps["items"] {
  const order: string[] = [];
  const byGroup = new Map<string, MenuItemConfig[]>();
  for (const item of items) {
    if (!byGroup.has(item.group)) {
      order.push(item.group);
      byGroup.set(item.group, []);
    }
    byGroup.get(item.group)!.push(item);
  }
  // `ui.md` §2.1: «Группа показывается, только если роли доступен хотя бы
  // один её пункт» — здесь это выполняется автоматически: группа попадает
  // в `byGroup` только если у неё есть хотя бы один элемент `items`
  // (уже отфильтрованных по роли, `menuItemsForRole`).
  return order.map((group) => ({
    key: group,
    label: group,
    type: "group" as const,
    children: byGroup.get(group)!.map((item) => ({ key: item.path, label: item.label })),
  }));
}

/** Оболочка приложения (`ui.md` §2.1–2.4): боковое меню, отфильтрованное
 * ролью, шапка с хлебными крошками и блоком пользователя, баннер режима
 * внедрения. `must_change_password`/загрузка `/me` уже разобраны в
 * `SessionGate` — сюда приложение попадает только с готовой сессией. */
export function AppShell() {
  const me = useSession();
  const role = asRole(me.role);
  const navigate = useNavigate();
  const location = useLocation();

  const items = menuItemsForRole(role);
  const currentItem = items.find((item) => item.path === location.pathname);

  const handleLogout = useLogout();

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider theme="dark">
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={currentItem ? [currentItem.path] : []}
          items={buildMenuItems(items)}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <Breadcrumb
            items={[
              { title: "Система учёта отпусков" },
              ...(currentItem ? [{ title: currentItem.label }] : []),
            ]}
          />
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <Typography.Text style={{ color: "#fff" }}>{roleLabel(role)}</Typography.Text>
            <Button onClick={handleLogout}>Выйти</Button>
          </div>
        </Header>
        {/* `ui.md` §4: баннер виден только HR-администратору и
            администратору системы — рядовой роли он ничего не объясняет. */}
        {(role === "hr_admin" || role === "system_admin") && (
          <EmbeddedModeBanner visible={me.embedded_mode} />
        )}
        <Content style={{ padding: 24 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
