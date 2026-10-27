import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Col, Form, Input, InputNumber, Row, Space, Switch, Typography } from "antd";
import { useState } from "react";

import { apiClient } from "../../api/client";
import { stringDetail } from "../../api/errors";
import { formatDateTime } from "../../app/format";
import { EmptyState, ErrorLoadingState, LoadingState } from "../../app/states";
import {
  LDAP_FORM_FIELD_COUNT,
  defaultFormValues,
  formValuesFromSaved,
  hasUnsavedChanges,
  lastTestStatusLabel,
  toLdapSettingsIn,
  toLdapTestRequest,
} from "../../entities/ldapSettings";
import type { LdapFormValues, LdapSettingsOut, LdapTestResponse } from "../../entities/ldapSettings";
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
  const queryClient = useQueryClient();
  const [form] = Form.useForm<LdapFormValues>();
  const [actionError, setActionError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<LdapTestResponse | null>(null);
  // Снимок полей формы, реактивный к каждому изменению, а не прочитанный
  // один раз: от него зависит и подпись под кнопкой «Тестовое
  // подключение» (что именно она проверит ПРЯМО СЕЙЧАС), и тело запроса
  // в её обработчике — оба обязаны согласованно реагировать на любой
  // ввод, а не только на явную отправку формы (как `onFinish` у
  // «Сохранить»).
  const watchedValues = Form.useWatch([], form) as LdapFormValues | undefined;
  const currentValues = watchedValues ?? initialValues;
  // Решение (`T-M1-21`, принято и объяснено в докстринге экрана): кнопка
  // проверяет ровно то, что админ видит перед собой — черновые поля,
  // если они расходятся с сохранённой записью, иначе саму сохранённую
  // запись.
  const isDraftTest = hasUnsavedChanges(currentValues, saved);
  // Дата показывается только вместе с РЕАЛЬНО зафиксированным статусом —
  // без неё `saved && saved.last_test_status === null` (запись сохранена,
  // но ещё ни разу не проверена) выглядело бы как «— — Ещё не
  // проверялось», с лишним тире от `formatDateTime(null)`.
  const lastCheckLabel =
    saved && saved.last_test_status !== null
      ? `Последняя зафиксированная проверка: ${formatDateTime(saved.last_tested_at)} — ${lastTestStatusLabel(saved.last_test_status)}`
      : `Последняя зафиксированная проверка: ${lastTestStatusLabel(null)}`;

  const saveMutation = useMutation({
    mutationFn: async (values: LdapFormValues) => {
      const { error } = await apiClient.PUT("/api/admin/ldap-settings", {
        body: toLdapSettingsIn(values),
      });
      if (error) {
        throw new Error(stringDetail(error.detail) ?? "Не удалось сохранить параметры подключения.");
      }
    },
    onSuccess: async () => {
      setActionError(null);
      // Поле пароля не обязано держать только что отправленное значение
      // дольше, чем нужно было для самой отправки (`NFR-SEC-001`) — после
      // успешного сохранения оно снова пусто, тем же смыслом «не менять»,
      // что и всегда.
      form.setFieldValue("bind_password", "");
      // `PUT` всегда обнуляет `last_tested_at`/`last_test_status`
      // (`upsert_ldap_settings`) и может поменять `is_active` — оба
      // способны сдвинуть `is_embedded_mode` (`backend/app/modules/auth/
      // embedded_mode.py`), поэтому инвалидируются оба кэша: параметры
      // (сам экран, зафиксированный результат проверки) и `["me"]`
      // (баннер режима внедрения в оболочке, `T-M1-18`). Инвалидация, не
      // правка кэша по ответу `PUT` вслепую (тот же принцип, что и в
      // `T-M1-22`: подтверждённое состояние приходит отдельным `GET`).
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["ldap-settings"] }),
        queryClient.invalidateQueries({ queryKey: ["me"] }),
      ]);
    },
    onError: (error) => {
      setActionError(error instanceof Error ? error.message : "Не удалось сохранить параметры подключения.");
    },
  });

  const handleSave = (values: LdapFormValues) => {
    setTestResult(null);
    saveMutation.mutate(values);
  };

  const testMutation = useMutation({
    mutationFn: async () => {
      const { data, error } = await apiClient.POST("/api/admin/ldap-settings/test", {
        body: toLdapTestRequest(currentValues, isDraftTest),
      });
      if (error) {
        throw new Error(stringDetail(error.detail) ?? "Не удалось проверить подключение.");
      }
      return { data, wasDraft: isDraftTest };
    },
    onSuccess: async ({ data, wasDraft }) => {
      setActionError(null);
      setTestResult(data);
      // Пустое тело проверяло СОХРАНЁННУЮ запись — сервер зафиксировал
      // success/failure в `last_tested_at`/`last_test_status`
      // (`_verify_saved_ldap_settings`) независимо от исхода, и оба могут
      // сдвинуть `is_embedded_mode` в любую сторону (успех снимал режим
      // внедрения, повторный провал после прежнего успеха вернул бы его
      // — поэтому инвалидация не только на success). Черновая проверка
      // (`wasDraft`) ничего не пишет на сервере — инвалидировать нечего.
      if (!wasDraft) {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["ldap-settings"] }),
          queryClient.invalidateQueries({ queryKey: ["me"] }),
        ]);
      }
    },
    onError: (error) => {
      setTestResult(null);
      setActionError(error instanceof Error ? error.message : "Не удалось проверить подключение.");
    },
  });

  const handleTest = () => {
    setActionError(null);
    testMutation.mutate();
  };

  return (
    <div>
      <Typography.Paragraph type="secondary">{lastCheckLabel}</Typography.Paragraph>

      <Form<LdapFormValues>
        form={form}
        layout="vertical"
        initialValues={initialValues}
        onFinish={handleSave}
        onValuesChange={() => setTestResult(null)}
      >
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
              extra={
                saved
                  ? "Сохранённый пароль нигде не показывается. Пустое поле — оставить прежний пароль без изменений."
                  : "Параметры ещё не сохранялись — без пароля проверить подключение не получится."
              }
            >
              <Input.Password autoComplete="new-password" />
            </Form.Item>
            <Form.Item label="LDAP включён" name="is_active" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Col>
        </Row>

        {actionError && (
          <Alert type="error" message={actionError} showIcon style={{ marginBottom: 16 }} />
        )}

        <Form.Item>
          <Space direction="vertical">
            <Space>
              <Button type="primary" htmlType="submit" loading={saveMutation.isPending}>
                Сохранить
              </Button>
              <Button htmlType="button" onClick={handleTest} loading={testMutation.isPending}>
                Тестовое подключение
              </Button>
            </Space>
            <Typography.Text type="secondary">
              {isDraftTest
                ? "Будут проверены введённые, но ещё не сохранённые значения — само сохранение не выполняется."
                : "Будет проверена сохранённая запись; результат зафиксируется на сервере."}
            </Typography.Text>
            {testResult && (
              <Alert
                type={testResult.success ? "success" : "error"}
                message={testResult.detail}
                showIcon
              />
            )}
          </Space>
        </Form.Item>
      </Form>
    </div>
  );
}

/** Раздел «Администрирование → LDAP» (`T-M1-21`, `FR-ADMIN-005`,
 * `FR-AUTH-003`). Тип экрана — «Форма настроек» (`ui.md` §3): поля в две
 * колонки, пояснение под полем, действия внизу слева. Доступ —
 * `system_admin`: проверено, что это совпадает с правами всех трёх
 * серверных эндпоинтов (`require_roles("system_admin")`,
 * `ldap_settings_router.py`) и с `MENU_ITEMS`'s `admin-ldap` (`roles.ts`)
 * — в отличие от производственного календаря (`T-M1-22`), расхождения
 * здесь нет и чинить нечего.
 *
 * `GET` отвечает `null`, если параметры ни разу не сохраняли — это не
 * ошибка и не пустая форма без объяснений, а состояние «Пусто» (`ui.md`
 * §4): иконка, заголовок, пояснение, кнопка основного действия
 * («Настроить подключение»), которая просто открывает форму с пустыми/
 * дефолтными значениями (`defaultFormValues`).
 *
 * Сохранение (`PUT`) отправляет ПОЛНЫЙ состав формы — поле пароля
 * обрабатывается отдельным правилом (опасность 2): ключ `bind_password`
 * ОТСУТСТВУЕТ в теле, если поле пусто, а не равен `""`. Причина — в
 * `toLdapSettingsIn` (`entities/ldapSettings.ts`), не здесь: одна копия
 * объяснения, не две (вторая и была той, что устарела незамеченной при
 * T-M1-33 — PR #168 review). После успеха поле пароля снова пустое
 * (`NFR-SEC-001` — значение не должно жить в состоянии дольше, чем нужно
 * было для самой отправки), а оба кэша — `["ldap-settings"]` и `["me"]`
 * — инвалидируются, не правятся по ответу `PUT` вслепую: любое
 * сохранение обнуляет `last_tested_at`/`last_test_status`
 * (`upsert_ldap_settings`) и может поменять `is_active`, а оба способны
 * сдвинуть `is_embedded_mode` (`backend/app/modules/auth/
 * embedded_mode.py`) — без инвалидации `["me"]` баннер режима внедрения
 * (`T-M1-18`) разошёлся бы с действительностью (опасность 3). Ошибка
 * сервера показывается текстом сервера, как есть, над кнопками действия
 * (`ui.md` §7, «Место ошибки» — то же правило, которое `PR #158 review`
 * проверило на действующем экране производственного календаря).
 *
 * Пароль служебной записи (`NFR-SEC-001`, `ui.md` §7): сервер НИКОГДА не
 * присылает его в ответе (`LdapSettingsOut` — поля `bind_password` нет
 * вовсе), поэтому поле формы всегда начинается пустым, независимо от
 * того, сохранён пароль или нет, — значение неоткуда взять, а не скрыто
 * сознательно. Пояснение под полем ЗАВИСИТ от того, есть ли сохранённая
 * запись (`saved`): для неё — что пустое поле при сохранении значит «не
 * менять» (без этого текста экран выглядел бы так, будто пароль
 * потерялся, и администратор перенабирал бы его при каждой правке порта);
 * при первичной настройке (`saved === null`) текст про «прежний пароль»
 * был бы неверен — прежнего не существует, и статичный текст лгал бы в
 * этот момент. Вместо этого — что пароль нужен для проверки: проверено
 * по `_test_draft_ldap_params` (`backend/app/modules/admin/
 * ldap_settings_service.py`) — без сохранённой записи взять пароль
 * неоткуда, кроме самого поля, и пустое поле в этом случае обрывает
 * проверку отказом «Укажите все параметры подключения для проверки»
 * (`PR #159 review`: прежняя статичная подсказка противоречила этому
 * поведению именно в момент первой настройки).
 *
 * Зафиксированный результат последней проверки (`last_tested_at`,
 * `last_test_status`) показан отдельно от формы и читает ЖИВОЕ значение
 * кэша запроса (`LdapSettingsForm`'s `saved`, не `initialValues`) — это
 * состояние СИСТЕМЫ (от него зависит режим внедрения, `is_embedded_mode`,
 * `backend/app/modules/auth/embedded_mode.py`), а не разовый результат
 * нажатия кнопки «Тестовое подключение» (`testResult`, показан рядом с
 * самой кнопкой, своим отдельным `Alert`) — путать их в одном месте
 * нельзя: первое описывает состояние системы, второе — только что
 * произведённое действие. `last_tested_at` — единственное поле
 * `datetime.datetime` (не `date`) во всей схеме сегодня; `ui.md` §6
 * формата времени не определяет — `formatDateTime` (`app/format.ts`)
 * расширяет формат дат §6 временем, не подменяет его.
 *
 * `testResult` сбрасывается ЛЮБОЙ правкой поля (`Form`'s `onValuesChange`,
 * не только явным сохранением или неудачной проверкой) — без этого отметка
 * «Соединение установлено» о черновых параметрах продолжала бы висеть
 * рядом с полями после того, как админ поправил ещё одно значение, то
 * есть описывала бы уже не то, что показано на экране (`PR #159 review`).
 *
 * «Тестовое подключение» (`T-M1-21`, решение принято здесь): у серверной
 * проверки (`POST /ldap-settings/test`, `ldap_settings_service.
 * test_ldap_connection`) два режима, различаемых по телу запроса — пустое
 * проверяет СОХРАНЁННУЮ запись и фиксирует исход на сервере, непустое —
 * ЧЕРНОВЫЕ поля и ничего не сохраняет. Одна кнопка выбирает между ними по
 * состоянию формы (`hasUnsavedChanges`, `entities/ldapSettings.ts`): есть
 * несохранённые изменения (включая непустое поле пароля — сохранённый
 * пароль клиенту вообще не виден, так что заполненное поле не может
 * значить ничего другого, кроме черновой правки) — проверяются именно
 * они; форма совпадает с сохранённой записью — проверяется сама запись.
 * В обоих случаях кнопка проверяет РОВНО то, что админ видит перед собой
 * — пояснение под кнопкой (`ui.md` §3, «пояснение под полем», применённое
 * здесь к действию) называет, какой из двух случаев сейчас активен, не
 * оставляя угадывать.
 *
 * Проверка сохранённой записи инвалидирует оба кэша (`["ldap-settings"]`,
 * `["me"]`) ПОСЛЕ ЛЮБОГО исхода, а не только после успеха: сервер
 * обновляет `last_test_status` и при успехе, и при провале
 * (`_verify_saved_ldap_settings` пишет `"success"`/`"failure"` в обоих
 * случаях), и оба способны сдвинуть `is_embedded_mode` в любую сторону —
 * повторный провал после прежнего успешного теста обязан вернуть баннер
 * режима внедрения, а не оставить его погашенным по старым данным.
 * Черновая проверка ничего не пишет на сервере — инвалидировать нечего,
 * и кнопка в этом случае никогда не затрагивает сохранённое состояние.
 * Проверка не сохраняет параметры ни при каких условиях (`ui.md` §3,
 * «Кнопка проверки... не сохраняет») — `handleTest` не вызывает
 * `saveMutation` ни прямо, ни косвенно; это же — обязательный
 * отрицательный контроль теста (коммит 3). */
export function LdapSettingsPage() {
  const query = useLdapSettingsQuery();
  const [started, setStarted] = useState(false);

  if (query.isLoading) {
    return <LoadingState shape={{ kind: "form", fields: LDAP_FORM_FIELD_COUNT }} />;
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
