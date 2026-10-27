import { Card, Descriptions, Space, Table, Typography } from "antd";

import { NO_ACCESS_MESSAGE } from "../app/accessMessage";
import { formatDate } from "../app/format";
import { EmptyState, ErrorLoadingState, LoadingState, NoAccessState } from "../app/states";
import { useAccess, useGoHome } from "../app/useAccess";
import { useSession } from "../app/useSession";
import {
  entryTypeLabel,
  formatAmount,
  type BalanceEntry,
  type LeaveTypeBalance,
} from "../entities/balance";
import { BalancesLoadError, useMyBalancesQuery } from "../entities/useMyBalancesQuery";

/** «Мой отпуск» (`FR-BAL-002`, `T-M2-13`). Тип экрана по `ui.md` §3 - «Карточка записи»:
 * шапка с названием раздела и датой, под ней блок на каждый вид отпуска (остаток и границы
 * рабочего года) и таблица истории событий. Не «Список с фильтрами» (фильтров и выбора записи
 * нет), не форма и не мастер.
 *
 * Единственный источник - `GET /api/employees/{id}/balances`; на фронтенде НЕТ расчёта (ни
 * сумм, ни разниц): показывается то, что посчитал сервер. Единица - из ответа, подпись одна
 * на всё приложение (`entities/balance.ts`). Выбора даты нет сознательно: пока не сделана
 * `T-M2-21`, остаток на дату внутри текущего рабочего года занижен. */
export function MyLeavePage() {
  const me = useSession();
  const { hasEmployeeCard } = useAccess();
  const goHome = useGoHome();
  const query = useMyBalancesQuery(me.employee_id);

  // ОСОЗНАННЫЙ второй рубеж (защита в глубину), а не забытая проверка: основная защита -
  // `RoleGuard` на маршруте, вторая - `enabled` в хуке запроса (без карточки он не уходит);
  // эта ветка нужна, чтобы страница без карточки показала «Нет доступа», а не «Остатков нет».
  if (!hasEmployeeCard) {
    return <NoAccessState message={NO_ACCESS_MESSAGE} onGoHome={goHome} />;
  }
  if (query.isLoading) {
    // ОЦЕНКА, не сверяется с итогом: число блоков (видов отпуска) приходит с ответом; основной
    // вид есть всегда (защищён, `T-M2-10`), остальные добавятся после загрузки.
    return <LoadingState shape={{ kind: "card", blocks: 1 }} />;
  }
  if (query.isError) {
    const error = query.error;
    if (error instanceof BalancesLoadError && error.status === 403) {
      return (
        <NoAccessState
          message={error.detail ?? NO_ACCESS_MESSAGE}
          onGoHome={goHome}
        />
      );
    }
    // `cancelRefetch: false` задуман так, чтобы повторное нажатие, пока запрос в полёте, не
    // запускало второй (этот `GET` пишет в журнал). НЕ ПРОВЕРЕНО: тест от умолчания этот флаг
    // не отличает - после ошибки без данных кнопка исчезает под каркасом загрузки, и быстрые
    // нажатия не накапливаются и без него. Оставлено подстраховкой, а не проверенной защитой.
    return <ErrorLoadingState onRetry={() => query.refetch({ cancelRefetch: false })} />;
  }

  const balances = query.data?.balances ?? [];
  if (balances.length === 0) {
    return (
      <EmptyState
        title="Остатков пока нет"
        description="По вашей карточке ещё нет видов отпуска, по которым ведётся остаток."
      />
    );
  }

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div>
        <Typography.Title level={3} style={{ margin: 0 }}>
          Мой отпуск
        </Typography.Title>
        <Typography.Text type="secondary">
          Остаток на {formatDate(query.data?.as_of ?? null)}
        </Typography.Text>
      </div>
      {balances.map((balance) => (
        <LeaveTypeCard key={balance.leave_type_id} balance={balance} />
      ))}
    </Space>
  );
}

function LeaveTypeCard({ balance }: { balance: LeaveTypeBalance }) {
  const yearRange =
    balance.working_year_start && balance.working_year_end
      ? `${formatDate(balance.working_year_start)} — ${formatDate(balance.working_year_end)}`
      : "—";

  return (
    <Card title={balance.name} aria-label={balance.name}>
      <Descriptions column={{ xs: 1, md: 3 }} size="small">
        <Descriptions.Item label="Остаток за текущий рабочий год">
          {formatAmount(balance.balance, balance.unit)}
        </Descriptions.Item>
        <Descriptions.Item label="Рабочий год">{yearRange}</Descriptions.Item>
        <Descriptions.Item label="Всего по всем рабочим годам">
          {formatAmount(balance.total_balance, balance.unit)}
        </Descriptions.Item>
      </Descriptions>
      <Typography.Title level={5}>История</Typography.Title>
      <Table<BalanceEntry>
        size="small"
        rowKey="id"
        dataSource={balance.history}
        pagination={{ pageSize: 10, hideOnSinglePage: true }}
        locale={{ emptyText: "Событий пока нет" }}
        columns={[
          { title: "Дата", dataIndex: "entry_date", render: (value: string) => formatDate(value) },
          {
            title: "Тип",
            dataIndex: "entry_type",
            render: (value: string) => entryTypeLabel(value),
          },
          {
            title: "Количество",
            dataIndex: "quantity",
            align: "right",
            render: (value: string) => formatAmount(value, balance.unit, { signed: true }),
          },
          { title: "Причина", dataIndex: "reason", render: (value: string | null) => value ?? "—" },
          { title: "Кто", dataIndex: "actor_name" },
        ]}
      />
    </Card>
  );
}
