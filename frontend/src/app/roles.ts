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
  group: "Справочники" | "Администрирование";
}

// Состав и порядок — таблица `ui.md` §2.2, строки со стадией M1. Остальные
// разделы таблицы (M2–M6) здесь не появляются вовсе — они не реализованы,
// а не просто скрыты (`ui.md` §2.1).
export const MENU_ITEMS: readonly MenuItemConfig[] = [
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
    roles: ["hr_admin", "system_admin"],
    group: "Администрирование",
  },
];

export function menuItemsForRole(role: Role): MenuItemConfig[] {
  return MENU_ITEMS.filter((item) => item.roles.includes(role));
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
  { path: "/employees/:id", roles: ["hr_admin", "system_admin"] },
];

export function isPathAllowedForRole(role: Role, path: string): boolean {
  const item =
    MENU_ITEMS.find((candidate) => candidate.path === path) ??
    NON_MENU_ROUTES.find((candidate) => candidate.path === path);
  // Путь вне обоих реестров — запрет, не разрешение: правильная сторона
  // умолчания для проверки доступа — новый маршрут ОБЯЗАН явно объявить
  // свои роли в одном из них, а не получить доступ молча тем, что забыли
  // его туда добавить.
  if (!item) {
    return false;
  }
  return item.roles.includes(role);
}

/** Стартовая страница роли (`ui.md` §2.3). `system_admin`'s дефолт
 * («Администрирование → LDAP») уже существует в M1 — используется как
 * есть. У `hr_admin` дефолт («Мой отпуск») ещё не существует — берётся
 * первый доступный роли пункт меню (`ui.md` §2.2, порядок таблицы:
 * «Сотрудники»). У `employee`/`accountant` в M1 нет ни одного пункта —
 * `null` означает страницу-заглушку (`ui.md` §2.3, правка `T-M1-18`). */
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

export function defaultPathForRole(role: Role): string | null {
  if (role === "system_admin") {
    return "/admin/ldap-settings";
  }
  const items = menuItemsForRole(role);
  return items.length > 0 ? items[0].path : null;
}
