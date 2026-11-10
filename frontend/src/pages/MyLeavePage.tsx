import { Card, Descriptions, Space, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";

import { NO_ACCESS_MESSAGE } from "../app/accessMessage";
import { formatDate } from "../app/format";
import { EmptyState, ErrorLoadingState, LoadingState, NoAccessState } from "../app/states";
import { useAccess, useGoHome } from "../app/useAccess";
import { useSession } from "../app/useSession";
import {
  entryTypeLabel,
  formatAmount,
  historyRows,
  type EntryType,
  type HistoryRow,
  type LeaveTypeBalance,
  type Unit,
} from "../entities/balance";
import { BalancesLoadError, useMyBalancesQuery } from "../entities/useMyBalancesQuery";

// Размер страницы истории - ОДНО число и для таблицы, и для каркаса загрузки (`ui.md` §4).
const HISTORY_PAGE_SIZE = 10;

function historyColumns(unit: Unit): ColumnsType<HistoryRow> {
  return [
    { title: "Дата", dataIndex: "entry_date", render: (value: string) => formatDate(value) },
    { title: "Тип", dataIndex: "entry_type", render: (value: EntryType) => entryTypeLabel(value) },
    {
      title: "Количество",
      dataIndex: "quantity",
      align: "right",
      render: (value: string) => formatAmount(value, unit, { signed: true }),
    },
    { title: "Причина", dataIndex: "reason", render: (value: string | null) => value ?? "—" },
    { title: "Кто", dataIndex: "actor_name", render: (value: string | null) => value ?? "—" },
  ];
}

/** «Мой отпуск» (`FR-BAL-002`, `T-M2-13`). Тип экрана по `ui.md` §3 - «Карточка записи»:
 * шапка с названием раздела и датой, под ней блок на каждый вид отпуска (остаток и границы
 * рабочего года) и таблица истории событий. Не «Список с фильтрами» (фильтров и выбора записи
 * нет), не форма и не мастер.
 *
 * Единственный источник - `GET /api/employees/{id}/balances`; на фронтенде НЕТ расчёта (ни
 * сумм, ни разниц): показывается то, что посчитал сервер. История - свежие сверху, начисления
 * рабочего года одной строкой с итогом сервера (`T-M2-30`, `ui.md` §6, `historyRows`). Единица - из ответа, подпись одна
 * на всё приложение (`entities/balance.ts`). Выбора даты нет: основание «остаток на дату внутри
 * года занижен» снято `T-M2-21`, элемент вынесен в `T-M2-31`. */
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
    // Каркас из ДВУХ вызовов одного компонента: блок вида (остаток и границы года) и таблица
    // истории под ним, как у каждого вида после загрузки. Таблица истории сверяется с итогом для
    // одного вида (`app/loadingShapeParity.test.tsx`). ОЦЕНКА: число блоков (видов отпуска)
    // приходит с ответом - основной вид есть всегда (защищён, `T-M2-10`), остальные добавятся
    // после загрузки; число строк истории тоже неизвестно - взята полная страница, у короткой
    // истории экран после загрузки будет ниже. После свёртки начислений (`T-M2-30`) история
    // обычно короче (строка на рабочий год плюс редкие события), и расхождение чаще, чем
    // прежде; оценка («полная страница») не менялась.
    return (
      <>
        <LoadingState shape={{ kind: "card", blocks: 1 }} />
        {/* единица нужна только для сборки колонок: число колонок от неё не зависит */}
        <LoadingState
          shape={{
            kind: "table",
            rows: HISTORY_PAGE_SIZE,
            columns: historyColumns("working_days").length,
          }}
        />
      </>
    );
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
      <Table<HistoryRow>
        size="small"
        rowKey="key"
        dataSource={historyRows(balance)}
        pagination={{ pageSize: HISTORY_PAGE_SIZE, hideOnSinglePage: true }}
        locale={{ emptyText: "Событий пока нет" }}
        columns={historyColumns(balance.unit)}
      />
    </Card>
  );
}
