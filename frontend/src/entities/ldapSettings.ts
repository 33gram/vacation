import type { components } from "../api/generated/schema";

export type LdapSettingsOut = components["schemas"]["LdapSettingsOut"];
export type LdapSettingsIn = components["schemas"]["LdapSettingsIn"];
export type LdapTestRequest = components["schemas"]["LdapTestRequest"];
export type LdapTestResponse = components["schemas"]["LdapTestResponse"];

// `backend/app/core/ldap_client.py`: DEFAULT_LDAP_PORT, DEFAULT_USER_MATCH_ATTRIBUTE.
export const DEFAULT_LDAP_PORT = 389;
export const DEFAULT_USER_MATCH_ATTRIBUTE = "mail";

/** Значения формы — отдельный тип от `LdapSettingsIn`/`LdapSettingsOut`:
 * пароль в форме всегда начинается пустым (`ui.md` §7 — сохранённый
 * пароль нигде не показывается) и имеет у каждой из двух серверных схем
 * свой смысл, не совпадающий со смыслом поля формы (у `Out` пароля нет
 * вовсе, у `In` пустое/отсутствующее значит «не менять»). */
export interface LdapFormValues {
  server_url: string;
  port: number;
  base_dn: string;
  bind_dn: string;
  bind_password: string;
  user_match_attribute: string;
  is_active: boolean;
}

/** Начальные значения для записи, которую ещё ни разу не сохраняли
 * (`GET` вернул `null`) — поле пароля в этом случае тоже пустое, но
 * здесь это не «оставить прежний» (прежнего нет), а просто «ещё не
 * введён». */
export function defaultFormValues(): LdapFormValues {
  return {
    server_url: "",
    port: DEFAULT_LDAP_PORT,
    base_dn: "",
    bind_dn: "",
    bind_password: "",
    user_match_attribute: DEFAULT_USER_MATCH_ATTRIBUTE,
    is_active: false,
  };
}

export function formValuesFromSaved(saved: LdapSettingsOut): LdapFormValues {
  return {
    server_url: saved.server_url,
    port: saved.port,
    base_dn: saved.base_dn,
    bind_dn: saved.bind_dn,
    bind_password: "",
    user_match_attribute: saved.user_match_attribute,
    is_active: saved.is_active,
  };
}

const LAST_TEST_STATUS_LABELS: Record<string, string> = {
  success: "Успешно",
  failure: "Неуспешно",
};

/** Подпись зафиксированного результата последней проверки СОХРАНЁННОЙ
 * записи (`LdapSettingsOut.last_test_status`) — это состояние системы
 * (`is_embedded_mode`, `backend/app/modules/auth/embedded_mode.py`
 * читает именно это поле), а не разовый результат нажатия кнопки
 * «Тестовое подключение»: разовый результат живёт отдельно, в ответе
 * мутации, и эта функция его не описывает. */
export function lastTestStatusLabel(status: string | null): string {
  if (status === null) {
    return "Ещё не проверялось";
  }
  return LAST_TEST_STATUS_LABELS[status] ?? status;
}

/** Есть ли в форме несохранённые изменения относительно `saved` — от
 * этого зависит, что проверяет кнопка «Тестовое подключение» (`T-M1-21`,
 * коммит 3): черновые поля формы или сохранённую запись
 * (`ldap_settings_service.test_ldap_connection` — пустое тело проверяет
 * сохранённое, непустое — черновое, `backend/app/modules/admin/
 * ldap_settings_service.py`). Пароль сравнивается иначе, чем остальные
 * поля: сохранённый пароль на клиент вообще не приходит (`ui.md` §7),
 * поэтому пустое поле пароля — единственное его состояние, которое можно считать
 * «не изменено»; что угодно другое — черновое значение. Если ничего не
 * сохранено (`saved === null`), это тоже несохранённое состояние
 * безусловно — сравнивать черновик не с чем. */
export function hasUnsavedChanges(values: LdapFormValues, saved: LdapSettingsOut | null): boolean {
  if (values.bind_password !== "") {
    return true;
  }
  if (saved === null) {
    return true;
  }
  return (
    values.server_url !== saved.server_url ||
    values.port !== saved.port ||
    values.base_dn !== saved.base_dn ||
    values.bind_dn !== saved.bind_dn ||
    values.user_match_attribute !== saved.user_match_attribute ||
    values.is_active !== saved.is_active
  );
}

/** Тело `PUT` — ключ `bind_password` ОТСУТСТВУЕТ, если поле формы пусто,
 * а не равен `""` (`T-M1-21`, опасность 2). Контракт схемы —
 * `None`/отсутствует ИЛИ пустая строка одинаково значат «пароль не
 * менять» (`LdapSettingsIn.bind_password: SecretStr | None = None`,
 * `backend/app/schemas/ldap_settings.py`, T-M1-33 — тип стал
 * `SecretStr`, контракт не изменился). Сервер сегодня проверяет это
 * явно (`is not None` + пустота РАСКРЫТОГО значения, не истинность
 * обёртки, `ldap_settings_service.upsert_ldap_settings`) — тоже
 * обрабатывает пустую строку как «не менять», поэтому опускать ключ
 * здесь не строго обязательно для корректности. Но клиент всё равно не
 * полагается на эту снисходительность отправкой `""`: опускает ключ
 * вовсе, чтобы НИКАКАЯ будущая серверная правка (например, наивное
 * ужесточение до одной лишь проверки `is not None`, без проверки
 * пустоты раскрытого значения) не превратила отправку пустой строки в
 * тихое стирание пароля — остаться корректным при более строгой
 * серверной проверке дешевле, чем заново находить этот риск. */
export function toLdapSettingsIn(values: LdapFormValues): LdapSettingsIn {
  return {
    server_url: values.server_url,
    port: values.port,
    base_dn: values.base_dn,
    bind_dn: values.bind_dn,
    ...(values.bind_password !== "" ? { bind_password: values.bind_password } : {}),
    user_match_attribute: values.user_match_attribute,
    is_active: values.is_active,
  };
}

/** Тело `POST /ldap-settings/test` — по решению, записанному в
 * докстринге `LdapSettingsPage`: форма без несохранённых изменений шлёт
 * пустое тело (проверяется сохранённая запись, сервер сам фиксирует
 * результат), иначе — заполненные черновые поля. Пароль — то же правило,
 * что и в `toLdapSettingsIn`: пустое поле — отсутствие ключа, а не `""`,
 * чтобы сервер подставил СОХРАНЁННЫЙ пароль вместо черновой пустой
 * строки (`_test_draft_ldap_params`). */
export function toLdapTestRequest(values: LdapFormValues, isDraft: boolean): LdapTestRequest {
  if (!isDraft) {
    return {};
  }
  return {
    server_url: values.server_url,
    port: values.port,
    base_dn: values.base_dn,
    bind_dn: values.bind_dn,
    ...(values.bind_password !== "" ? { bind_password: values.bind_password } : {}),
    user_match_attribute: values.user_match_attribute,
  };
}
