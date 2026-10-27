import type { components } from "../api/generated/schema";

// Единый источник состава пунктов меню и того, кому какой раздел доступен
// (`ui.md` §2.1–2.3) — используется и для рендера бокового меню
// (`AppShell`), и для guard'а маршрутов (`RoleGuard`), чтобы эти два места
// не могли разойтись в том, что считается «доступным разделом».

export type Role = "employee" | "hr_admin" | "accountant" | "system_admin";

/** `MeResponse.role` типизирован в схеме как голая `string` — backend не
 * публикует перечисление в OpenAPI. Значение гарантированно одно из
 * четырёх — ограничение `ck_accounts_role` на уровне БД (`models/
 * account.py`), а не соглашение клиента; приведение типа здесь опирается
 * на эту гарантию, а не изобретает свою. */
export function asRole(role: string): Role {
  return role as Role;
}

export interface MenuItemConfig {
  key: string;
  path: string;
  label: string;
  roles: readonly Role[];
  /** `null` - личный раздел: сверху, без заголовка группы (`ui.md` §2.2). */
  group: "Справочники" | "Администрирование" | null;
  /** Раздел самообслуживания: определяется НАЛИЧИЕМ КАРТОЧКИ сотрудника, а не ролью
   * (`requirements.md` §7 «Правило самообслуживания», §13 п. 22). У учётной записи без
   * карточки (`employee_id = null`, например bootstrap-администратор, `FR-AUTH-005`) пункта
   * нет вовсе. */
  requiresEmployeeCard?: boolean;
}

// Состав и порядок — таблица `ui.md` §2.2, строки со стадией M1. Остальные
// разделы таблицы (M2–M6) здесь не появляются вовсе — они не реализованы,
// а не просто скрыты (`ui.md` §2.1).
export const MENU_ITEMS: readonly MenuItemConfig[] = [
  {
    key: "my-leave",
    path: "/my-leave",
    label: "Мой отпуск",
    // Колонки таблицы `ui.md` §2.2 - роль ПРИ НАЛИЧИИ карточки: все четыре роли.
    roles: ["employee", "hr_admin", "accountant", "system_admin"],
    group: null,
    requiresEmployeeCard: true,
  },
  {
    key: "employees",
    path: "/employees",
    label: "Сотрудники",
    roles: ["hr_admin", "system_admin"],
    group: "Справочники",
  },
  {
    key: "departments",
    path: "/departments",
    label: "Подразделения",
    roles: ["hr_admin", "system_admin"],
    group: "Справочники",
  },
  {
    key: "admin-ldap",
    path: "/admin/ldap-settings",
    label: "LDAP",
    roles: ["system_admin"],
    group: "Администрирование",
  },
  {
    key: "admin-production-calendar",
    path: "/admin/production-calendar",
    label: "Производственный календарь",
    // T-M1-22 review: было ["hr_admin", "system_admin"], хотя оба
    // эндпоинта (`GET`/`PUT /api/admin/production-calendar/{year}`)
    // требуют `system_admin` (`require_roles("system_admin")`,
    // `production_calendar_router.py`) — hr_admin видел пункт меню,
    // открывал экран и получал 403 на первом же `GET`, попадая в
    // состояние «Ошибка загрузки» с бесполезной кнопкой «Повторить».
    // Меню приведено в соответствие с сервером, а не наоборот: если
    // календарь должен вести и HR, это решение про права на сервере,
    // не про то, кому показать пункт меню на клиенте.
    roles: ["system_admin"],
    group: "Администрирование",
  },
];

function isAvailable(item: MenuItemConfig, role: Role, hasEmployeeCard: boolean): boolean {
  return item.roles.includes(role) && (hasEmployeeCard || !item.requiresEmployeeCard);
}

export function menuItemsForRole(role: Role, hasEmployeeCard: boolean): MenuItemConfig[] {
  return MENU_ITEMS.filter((item) => isAvailable(item, role, hasEmployeeCard));
}

// T-M1-19: маршруты, доступные роли, но не являющиеся пунктом меню —
// карточка записи, форма создания. Отдельный от `MENU_ITEMS` реестр, а не
// расширение `RoleGuard` параметром ролей на каждом маршруте: права
// остаются в ЕДИНОМ месте (см. комментарий у `MENU_ITEMS` выше), и новый
// маршрут вне меню так же обязан явно себя зарегистрировать, как и пункт
// меню, — по той же логике «неизвестный путь запрещён» ниже. `/employees/
// {id}` сюда и добавлен — пунктом меню он не станет никогда, но роли на
// него те же, что и у самого раздела «Сотрудники».
interface NonMenuRouteConfig {
  path: string;
  roles: readonly Role[];
}

export const NON_MENU_ROUTES: readonly NonMenuRouteConfig[] = [
  { path: "/employees/new", roles: ["hr_admin", "system_admin"] },
  { path: "/employees/import", roles: ["hr_admin", "system_admin"] },
  { path: "/employees/:id", roles: ["hr_admin", "system_admin"] },
];

export function isPathAllowedForRole(role: Role, path: string, hasEmployeeCard: boolean): boolean {
  const menuItem = MENU_ITEMS.find((candidate) => candidate.path === path);
  if (menuItem) {
    return isAvailable(menuItem, role, hasEmployeeCard);
  }
  // Путь вне обоих реестров — запрет, не разрешение: правильная сторона
  // умолчания для проверки доступа — новый маршрут ОБЯЗАН явно объявить
  // свои роли в одном из них, а не получить доступ молча тем, что забыли
  // его туда добавить.
  const nonMenuRoute = NON_MENU_ROUTES.find((candidate) => candidate.path === path);
  return nonMenuRoute ? nonMenuRoute.roles.includes(role) : false;
}

const ROLE_LABELS: Record<Role, string> = {
  employee: "Сотрудник",
  hr_admin: "HR-администратор",
  accountant: "Бухгалтер",
  system_admin: "Администратор системы",
};

/** Название роли для отображения в шапке (`ui.md` §2.4) — не бизнес-
 * правило, чисто текст интерфейса. */
export function roleLabel(role: Role): string {
  return ROLE_LABELS[role];
}

/** Стартовая страница (`ui.md` §2.3): `system_admin` - «Администрирование → LDAP»
 * (доступен всегда); остальным - первый доступный пункт меню: «Мой отпуск» при наличии
 * карточки (он первый в `MENU_ITEMS`), иначе первый из остальных (HR без карточки -
 * «Сотрудники»). `null` - доступных разделов нет вовсе (`employee`/`accountant` без
 * карточки), это страница-заглушка. */
/** Доступ учётной записи, выведенный из сессии (`/api/auth/me`): роль, наличие карточки
 * сотрудника и стартовая страница. ОДНА формула на всё приложение (`AppShell`, `RoleGuard`,
 * `HomeRedirect`, `SessionGate`, страницы) - раньше `me.employee_id !== null` и вывод
 * стартовой страницы повторялись в пяти местах. `homePath === null` - доступных разделов нет
 * (страница-заглушка); места, которым нужен адрес, подставляют `"/"` сами. */
export function accessOf(me: components["schemas"]["MeResponse"]): {
  role: Role;
  hasEmployeeCard: boolean;
  homePath: string | null;
} {
  const role = asRole(me.role);
  const hasEmployeeCard = me.employee_id !== null;
  return { role, hasEmployeeCard, homePath: defaultPathForRole(role, hasEmployeeCard) };
}

export function defaultPathForRole(role: Role, hasEmployeeCard: boolean): string | null {
  if (role === "system_admin") {
    return "/admin/ldap-settings";
  }
  const items = menuItemsForRole(role, hasEmployeeCard);
  return items.length > 0 ? items[0].path : null;
}
