import { Breadcrumb, Button, Layout, Menu, Typography } from "antd";
import type { MenuProps } from "antd";
import { useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { EmbeddedModeBanner } from "./EmbeddedModeBanner";
import { menuItemsForRole, roleLabel, type MenuItemConfig } from "./roles";
import { useAccess } from "./useAccess";
import { useLogout } from "./useLogout";
import { useSession } from "./useSession";

const { Header, Sider, Content } = Layout;

function buildMenuItems(items: MenuItemConfig[]): MenuProps["items"] {
  // Личные разделы (`group: null`) - сверху, без заголовка группы (`ui.md` §2.2).
  const personal = items
    .filter((item) => item.group === null)
    .map((item) => ({ key: item.path, label: item.label }));
  const order: string[] = [];
  const byGroup = new Map<string, MenuItemConfig[]>();
  for (const item of items) {
    if (item.group === null) {
      continue;
    }
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
  return [
    ...personal,
    ...order.map((group) => ({
      key: group,
      label: group,
      type: "group" as const,
      children: byGroup.get(group)!.map((item) => ({ key: item.path, label: item.label })),
    })),
  ];
}

/** Оболочка приложения (`ui.md` §2.1–2.4): боковое меню, отфильтрованное
 * ролью, шапка с хлебными крошками и блоком пользователя, баннер режима
 * внедрения. `must_change_password`/загрузка `/me` уже разобраны в
 * `SessionGate` — сюда приложение попадает только с готовой сессией. */
export function AppShell() {
  const me = useSession();
  const { role, hasEmployeeCard } = useAccess();
  const navigate = useNavigate();
  const location = useLocation();
  // `ui.md` §5: «на узком экране боковое меню уезжает в выдвижную панель,
  // открываемую кнопкой в шапке» — `breakpoint`/`collapsedWidth` штатно
  // сворачивают `Sider` до нуля на узком экране (a) и синхронизируют это же
  // состояние обратно сюда через `onCollapse` (b), а `trigger={null}` глушит
  // собственную плавающую кнопку `Sider`а — вместо неё кнопка в шапке ниже
  // управляет тем же `collapsed` и работает одинаково что от breakpoint'а,
  // что вручную на широком экране (PR #144 review).
  const [siderCollapsed, setSiderCollapsed] = useState(false);

  const items = menuItemsForRole(role, hasEmployeeCard);
  const currentItem = items.find((item) => item.path === location.pathname);

  const handleLogout = useLogout();

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider
        theme="dark"
        breakpoint="lg"
        collapsedWidth={0}
        collapsed={siderCollapsed}
        onCollapse={setSiderCollapsed}
        trigger={null}
      >
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
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            {/* ui.md §9: подпись для скринридера у кнопки-иконки; §5: не
                меньше 44px на мобильном — единственный НОВЫЙ интерактивный
                элемент этого коммита, остальные элементы шапки/меню это
                правило пока не проверяют (не в объёме этой правки). */}
            <Button
              onClick={() => setSiderCollapsed((collapsed) => !collapsed)}
              aria-label={siderCollapsed ? "Показать меню" : "Скрыть меню"}
              style={{ minWidth: 44, minHeight: 44 }}
            >
              ☰
            </Button>
            <Breadcrumb
              items={[
                { title: "Система учёта отпусков" },
                ...(currentItem ? [{ title: currentItem.label }] : []),
              ]}
            />
          </div>
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
