import { Select, Typography } from "antd";
import { useMemo, useState } from "react";

import { formatDate } from "../../app/format";
import { EmptyState, ErrorLoadingState, LoadingState } from "../../app/states";
import { buildYearGrid, dayTypeLabel } from "../../entities/productionCalendar";
import type { CalendarDay } from "../../entities/productionCalendar";
import { useProductionCalendarQuery } from "../../entities/useProductionCalendarQuery";

const MONTH_NAMES = [
  "Январь",
  "Февраль",
  "Март",
  "Апрель",
  "Май",
  "Июнь",
  "Июль",
  "Август",
  "Сентябрь",
  "Октябрь",
  "Ноябрь",
  "Декабрь",
];

function dayOfMonth(isoDate: string): number {
  return Number(isoDate.slice(8, 10));
}

function monthIndexOf(isoDate: string): number {
  return Number(isoDate.slice(5, 7)) - 1;
}

// Цвет — не единственная метка типа (см. докстринг экрана), поэтому
// подобран для сигнала боковым зрением, а не как единственный источник
// информации: реальная метка — `title` у ячейки (`DayCell`).
function backgroundFor(dayType: string | null): string {
  if (dayType === "weekend") {
    return "#fff1b8";
  }
  if (dayType === "holiday") {
    return "#ffccc7";
  }
  if (dayType === "working") {
    return "#f5f5f5";
  }
  return "#ffffff"; // не задан
}

function DayCell({ day }: { day: CalendarDay }) {
  // `title` — атрибутивная метка типа (T-M1-22, коммит 2: «типы дней
  // различимы не только цветом»), а не только фон: цвет тестом не
  // проверить, и дальтонику он не поможет. Полная дата в подписи —
  // ДД.ММ.ГГГГ (`ui.md` §6), не голый ISO.
  const label = `${formatDate(day.date)} — ${dayTypeLabel(day.dayType)}`;
  return (
    <div
      title={label}
      style={{
        width: 28,
        height: 28,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 12,
        borderRadius: 4,
        background: backgroundFor(day.dayType),
      }}
    >
      {dayOfMonth(day.date)}
    </div>
  );
}

function MonthGrid({ monthIndex, days }: { monthIndex: number; days: CalendarDay[] }) {
  const monthDays = useMemo(
    () => days.filter((day) => monthIndexOf(day.date) === monthIndex),
    [days, monthIndex],
  );
  return (
    <div style={{ minWidth: 220 }}>
      <Typography.Text strong>{MONTH_NAMES[monthIndex]}</Typography.Text>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 8 }}>
        {monthDays.map((day) => (
          <DayCell key={day.date} day={day} />
        ))}
      </div>
    </div>
  );
}

const CURRENT_YEAR = new Date().getFullYear();
// «Минимум текущий и следующий» (T-M1-22) — T-M8-03 загружает календарь
// именно на эти два года; больше двух лет вперёд/назад в M1 не требуется,
// и добавлять их было бы не тем, что просит задача.
const YEAR_OPTIONS = [CURRENT_YEAR, CURRENT_YEAR + 1];

/** Раздел «Администрирование → Производственный календарь» (`T-M1-22`,
 * `FR-ADMIN-003`). Устройство и состояния — `ui.md` §3 (абзац, добавленный
 * этой же веткой) — экран не подпадает ни под один из четырёх типов.
 * Доступ — только `system_admin` (`require_roles("system_admin")` на
 * ОБОИХ эндпоинтах бэкенда; `roles.ts` приведён в соответствие этой же
 * веткой — было ошибочно открыто и `hr_admin`).
 *
 * `GET` отвечает 200 с пустым `days`, если год ещё не загружен, — НЕ 404
 * (`production_calendar_service.get_year` — обычный `SELECT`, без него не
 * различить «года не существует» от «федеральный сервер калькуляции ещё
 * не отвечал», оба свежие столбцы). Пустой массив — состояние «Пусто»
 * (`ui.md` §4), а не пустая сетка и не ошибка.
 *
 * День, ОТСУТСТВУЮЩИЙ в ответе сервера (`CalendarDay.dayType === null`,
 * `entities/productionCalendar.ts`), — решение принято здесь: этот экран
 * сам никогда не может породить такое состояние (сохранение всегда
 * отправляет ВЕСЬ год, ниже), но данные могут появиться и не через этот
 * экран (`T-M8-03`, прямой SQL) — частично заполненный год. Такой день
 * показывается отдельной, четвёртой меткой — «Не задан» (`dayTypeLabel`),
 * отличимой от всех трёх настоящих типов, а не молча подставленным
 * значением: подставить что-то самим значило бы придумать данные
 * календаря за администратора (правило 8). */
export function ProductionCalendarPage() {
  const [year, setYear] = useState(CURRENT_YEAR);
  const query = useProductionCalendarQuery(year);

  if (query.isLoading) {
    return <LoadingState />;
  }
  if (query.isError) {
    return <ErrorLoadingState onRetry={() => query.refetch()} />;
  }

  const serverDays = query.data?.days ?? [];
  const grid = buildYearGrid(year, serverDays);

  return (
    <div>
      <Typography.Title level={3}>Производственный календарь</Typography.Title>
      <Select
        aria-label="Год"
        value={year}
        onChange={setYear}
        options={YEAR_OPTIONS.map((option) => ({ value: option, label: String(option) }))}
        style={{ width: 120, marginBottom: 16 }}
      />

      {serverDays.length === 0 ? (
        <EmptyState
          title={`Календарь на ${year} год не заполнен`}
          description="Ни один день этого года ещё не загружен."
        />
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 24 }}>
          {MONTH_NAMES.map((_, monthIndex) => (
            <MonthGrid key={monthIndex} monthIndex={monthIndex} days={grid} />
          ))}
        </div>
      )}
    </div>
  );
}
