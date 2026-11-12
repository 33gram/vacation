import { Card, DatePicker, Descriptions, Space, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { useState } from "react";

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
  utcToday,
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
 * рабочего года) и таблица истории событий. Не «Список с фильтрами» (выбор даты - не фильтр
 * списка, а дата всего экрана), не форма и не мастер.
 *
 * Единственный источник - `GET /api/employees/{id}/balances`; на фронтенде НЕТ расчёта (ни
 * сумм, ни разниц): показывается то, что посчитал сервер. История - свежие сверху, начисления
 * рабочего года одной строкой с итогом сервера (`T-M2-30`, `ui.md` §6, `historyRows`). Единица - из ответа, подпись одна
 * на всё приложение (`entities/balance.ts`).
 *
 * ВЫБОР ДАТЫ (`T-M2-31`, `ui.md` §6): элемент стоит в шапке, ВНЕ блоков - при смене даты
 * блоки заменяются каркасом загрузки, а элемент остаётся (иначе он размонтировался бы под
 * пальцем). Прежние числа под новой датой не держатся (`keepPreviousData` не используется):
 * число рядом с датой, которой оно не принадлежит, хуже каркаса. Пусто - запрос без даты. Дата
 * хранится строкой `YYYY-MM-DD` из `dayjs.format`, не через `toISOString` (тот сдвигает сутки
 * в половине зон). Верхняя граница - сегодня по UTC (`utcToday`), как у сервера. ГРАНИЦА:
 * это единственная преграда будущей дате - сервер её принимает (`T-M2-31`, Р-5) и покажет
 * записанное, а не прогноз; у пользователя восточнее UTC в первые часы суток «сегодня» в
 * элементе - вчерашнее для него, у западнее вечером - уже завтрашнее. Экран не оговаривает,
 * что на старых данных разработки остаток на прошлые даты может быть занижен (годы,
 * записанные одним куском до `T-M2-21`, и закрытые до `T-M2-32`) - это не дефект экрана. */
const REQUEST_DAY_FORMAT = "YYYY-MM-DD";

export function MyLeavePage() {
  const me = useSession();
  const { hasEmployeeCard } = useAccess();
  const goHome = useGoHome();
  const [asOf, setAsOf] = useState<string | null>(null);
  const query = useMyBalancesQuery(me.employee_id, asOf);

  // ОСОЗНАННЫЙ второй рубеж (защита в глубину), а не забытая проверка: основная защита -
  // `RoleGuard` на маршруте, вторая - `enabled` в хуке запроса (без карточки он не уходит);
  // эта ветка нужна, чтобы страница без карточки показала «Нет доступа», а не «Остатков нет».
  if (!hasEmployeeCard) {
    return <NoAccessState message={NO_ACCESS_MESSAGE} onGoHome={goHome} />;
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
  }

  const loadedBalances = query.data?.balances ?? [];
  const notHired =
    loadedBalances.length > 0 && loadedBalances.every((balance) => balance.working_year_start === null);

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div>
        <Typography.Title level={3} style={{ margin: 0 }}>
          Мой отпуск
        </Typography.Title>
        <Space wrap align="center">
          <Typography.Text type="secondary">
            {query.data ? `Остаток на ${formatDate(query.data.as_of)}` : "Остаток на дату"}
          </Typography.Text>
          <DatePicker
            aria-label="Остаток на дату"
            placeholder="Сегодня"
            format="DD.MM.YYYY"
            allowClear
            value={asOf === null ? null : dayjs(asOf, REQUEST_DAY_FORMAT)}
            onChange={(day) => setAsOf(day ? day.format(REQUEST_DAY_FORMAT) : null)}
            disabledDate={(day) => day.format(REQUEST_DAY_FORMAT) > utcToday()}
          />
        </Space>
        {notHired && (
          // Одна строка в шапке, а не в каждом блоке вида: рабочего года нет у ВСЕХ видов сразу
          // (год - свойство даты и даты приёма, не вида), а блоков столько, сколько видов с
          // остатком. Сервер отдаёт `None` только при дате раньше приёма (`GET /balances`).
          <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
            На эту дату сотрудник ещё не принят: рабочего года нет, остаток нулевой.
          </Typography.Paragraph>
        )}
      </div>
      {renderBody(query)}
    </Space>
  );

  function renderBody(q: typeof query) {
    if (q.isLoading) {
      // Каркас из ДВУХ вызовов одного компонента: блок вида (остаток и границы года) и таблица
      // истории под ним, как у каждого вида после загрузки. Таблица истории сверяется с итогом для
      // одного вида (`app/loadingShapeParity.test.tsx`). ОЦЕНКА: число блоков (видов отпуска)
      // приходит с ответом - основной вид есть всегда (защищён, `T-M2-10`), остальные добавятся
      // после загрузки; число строк истории тоже неизвестно - взята полная страница, у короткой
      // истории экран после загрузки будет ниже. После свёртки начислений (`T-M2-30`) история
      // обычно короче (строка на рабочий год плюс редкие события), и расхождение чаще, чем
      // прежде; оценка («полная страница») не менялась. Тот же каркас показывается при смене даты.
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
    if (q.isError) {
      // `cancelRefetch: false` задуман так, чтобы повторное нажатие, пока запрос в полёте, не
      // запускало второй (этот `GET` пишет в журнал). НЕ ПРОВЕРЕНО: тест от умолчания этот флаг
      // не отличает - после ошибки без данных кнопка исчезает под каркасом загрузки, и быстрые
      // нажатия не накапливаются и без него. Оставлено подстраховкой, а не проверенной защитой.
      return <ErrorLoadingState onRetry={() => q.refetch({ cancelRefetch: false })} />;
    }
    const balances = q.data?.balances ?? [];
    if (balances.length === 0) {
      return (
        <EmptyState
          title="Остатков пока нет"
          description="По вашей карточке ещё нет видов отпуска, по которым ведётся остаток."
        />
      );
    }
    return (
      <>
        {balances.map((balance) => (
          <LeaveTypeCard key={balance.leave_type_id} balance={balance} />
        ))}
      </>
    );
  }
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
