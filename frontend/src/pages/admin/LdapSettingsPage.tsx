import { Col, Form, Input, InputNumber, Row, Switch, Typography } from "antd";
import { useState } from "react";

import { formatDateTime } from "../../app/format";
import { EmptyState, ErrorLoadingState, LoadingState } from "../../app/states";
import {
  defaultFormValues,
  formValuesFromSaved,
  lastTestStatusLabel,
} from "../../entities/ldapSettings";
import type { LdapFormValues, LdapSettingsOut } from "../../entities/ldapSettings";
import { useLdapSettingsQuery } from "../../entities/useLdapSettingsQuery";

/** Тело формы — поля в две колонки (`ui.md` §3, «Форма настроек»): слева
 * «куда подключаться» (адрес, порт, base DN, атрибут сопоставления),
 * справа «от чьего имени и включено ли» (служебная запись, пароль,
 * признак активности). `saved` отдельно от `initialValues`: `initialValues`
 * задаёт поля формы РОВНО один раз (`Form`'s `initialValues` читается
 * только при создании инстанса формы, antd это не скрывает) — после
 * коммита 2 правки переживают инвалидацию кэша, потому что сами и есть
 * источник нового сохранённого состояния; `saved`, напротив, всегда
 * читает ЖИВОЕ значение кэша `["ldap-settings"]`, поэтому зафиксированный
 * результат последней проверки обновляется сам, без ручной синхронизации
 * с полями формы. */
function LdapSettingsForm({
  saved,
  initialValues,
}: {
  saved: LdapSettingsOut | null;
  initialValues: LdapFormValues;
}) {
  const [form] = Form.useForm<LdapFormValues>();
  // Дата показывается только вместе с РЕАЛЬНО зафиксированным статусом —
  // без неё `saved && saved.last_test_status === null` (запись сохранена,
  // но ещё ни разу не проверена) выглядело бы как «— — Ещё не
  // проверялось», с лишним тире от `formatDateTime(null)`.
  const lastCheckLabel =
    saved && saved.last_test_status !== null
      ? `Последняя зафиксированная проверка: ${formatDateTime(saved.last_tested_at)} — ${lastTestStatusLabel(saved.last_test_status)}`
      : `Последняя зафиксированная проверка: ${lastTestStatusLabel(null)}`;

  return (
    <div>
      <Typography.Paragraph type="secondary">{lastCheckLabel}</Typography.Paragraph>

      <Form<LdapFormValues> form={form} layout="vertical" initialValues={initialValues}>
        <Row gutter={32}>
          <Col span={12} data-testid="ldap-settings-column-target">
            <Form.Item label="Адрес сервера" name="server_url" rules={[{ required: true }]}>
              <Input placeholder="ldap://dc.company.local" />
            </Form.Item>
            <Form.Item label="Порт" name="port" rules={[{ required: true }]}>
              <InputNumber style={{ width: "100%" }} min={1} max={65535} />
            </Form.Item>
            <Form.Item label="Base DN" name="base_dn" rules={[{ required: true }]}>
              <Input placeholder="dc=company,dc=local" />
            </Form.Item>
            <Form.Item
              label="Атрибут сопоставления пользователя"
              name="user_match_attribute"
              rules={[{ required: true }]}
              extra="По этому атрибуту каталога сопоставляется корпоративный e-mail сотрудника (FR-AUTH-001)."
            >
              <Input />
            </Form.Item>
          </Col>
          <Col span={12} data-testid="ldap-settings-column-credentials">
            <Form.Item
              label="Bind DN (служебная учётная запись)"
              name="bind_dn"
              rules={[{ required: true }]}
            >
              <Input placeholder="cn=svc-vacation,dc=company,dc=local" />
            </Form.Item>
            <Form.Item
              label="Пароль служебной записи"
              name="bind_password"
              extra="Сохранённый пароль нигде не показывается. Пустое поле — оставить прежний пароль без изменений."
            >
              <Input.Password autoComplete="new-password" />
            </Form.Item>
            <Form.Item label="LDAP включён" name="is_active" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Col>
        </Row>
      </Form>
    </div>
  );
}

/** Раздел «Администрирование → LDAP» (`T-M1-21`, `FR-ADMIN-005`,
 * `FR-AUTH-003`). Тип экрана — «Форма настроек» (`ui.md` §3): поля в две
 * колонки, пояснение под полем, действия внизу слева (действий в этом,
 * первом, коммите ещё нет — сохранение и проверка появятся следующими
 * коммитами; здесь только загрузка и отрисовка). Доступ — `system_admin`:
 * проверено, что это совпадает с правами всех трёх серверных эндпоинтов
 * (`require_roles("system_admin")`, `ldap_settings_router.py`) и с
 * `MENU_ITEMS`'s `admin-ldap` (`roles.ts`) — в отличие от производственного
 * календаря (`T-M1-22`), расхождения здесь нет и чинить нечего.
 *
 * `GET` отвечает `null`, если параметры ни разу не сохраняли — это не
 * ошибка и не пустая форма без объяснений, а состояние «Пусто» (`ui.md`
 * §4): иконка, заголовок, пояснение, кнопка основного действия
 * («Настроить подключение»), которая просто открывает форму с пустыми/
 * дефолтными значениями (`defaultFormValues`) — в этом коммите она ничего
 * не отправляет на сервер, это задел для коммита 2.
 *
 * Пароль служебной записи (`NFR-SEC-001`, `ui.md` §7): сервер НИКОГДА не
 * присылает его в ответе (`LdapSettingsOut` — поля `bind_password` нет
 * вовсе), поэтому поле формы всегда начинается пустым, независимо от
 * того, сохранён пароль или нет, — значение неоткуда взять, а не скрыто
 * сознательно. Пояснение под полем объясняет, что пустое при сохранении
 * значит «не менять»: без него экран выглядел бы так, будто пароль
 * потерялся, и администратор перенабирал бы его при каждой правке порта.
 *
 * Зафиксированный результат последней проверки (`last_tested_at`,
 * `last_test_status`) показан отдельно от формы и читает ЖИВОЕ значение
 * кэша запроса (`LdapSettingsForm`'s `saved`, не `initialValues`) — это
 * состояние СИСТЕМЫ (от него зависит режим внедрения, `is_embedded_mode`,
 * `backend/app/modules/auth/embedded_mode.py`), а не разовый результат
 * нажатия кнопки «Тестовое подключение» (её результат появится в
 * коммите 3 рядом с самой кнопкой — показывать его в этом же месте было
 * бы путать два разных факта: состояние системы и только что совершённое
 * действие). `last_tested_at` — единственное поле `datetime.datetime` (не
 * `date`) во всей схеме сегодня; `ui.md` §6 формата времени не определяет
 * — `formatDateTime` (`app/format.ts`) расширяет формат дат §6 временем,
 * не подменяет его. */
export function LdapSettingsPage() {
  const query = useLdapSettingsQuery();
  const [started, setStarted] = useState(false);

  if (query.isLoading) {
    return <LoadingState />;
  }
  if (query.isError) {
    return <ErrorLoadingState onRetry={() => query.refetch()} />;
  }

  const saved = query.data ?? null;

  if (saved === null && !started) {
    return (
      <EmptyState
        title="Подключение к LDAP не настроено"
        description="Параметры подключения к корпоративному каталогу ещё ни разу не сохранялись."
        action={{ label: "Настроить подключение", onClick: () => setStarted(true) }}
      />
    );
  }

  return (
    <div>
      <Typography.Title level={3}>Администрирование → LDAP</Typography.Title>
      <LdapSettingsForm
        saved={saved}
        initialValues={saved ? formValuesFromSaved(saved) : defaultFormValues()}
      />
    </div>
  );
}
