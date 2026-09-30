import type { components } from "../api/generated/schema";

export type ProductionCalendarDayOut = components["schemas"]["ProductionCalendarDayOut"];
export type ProductionCalendarDayIn = components["schemas"]["ProductionCalendarDayIn"];

/** Три известных значения `day_type` (`DayType` в схеме бэкенда,
 * `backend/app/schemas/production_calendar.py`). `ProductionCalendarDayOut.
 * day_type` в сгенерированной схеме типизирован как голая `string`
 * (FastAPI не публикует `Literal` для схемы ОТВЕТА, только для схемы
 * запроса, `ProductionCalendarDayIn`) — на экран может прийти значение вне
 * этих трёх, если формат когда-нибудь расширят на бэкенде раньше, чем на
 * клиенте; такое значение показывается как есть (см. `dayTypeLabel`), а не
 * приводит к падению экрана. */
export type KnownDayType = "working" | "weekend" | "holiday";

export const DAY_TYPE_LABELS: Record<KnownDayType, string> = {
  working: "Рабочий",
  weekend: "Выходной",
  holiday: "Праздник",
};

/** Порядок переключения по клику (`T-M1-22`, коммит 3) — три известных
 * типа по кругу. Незаданный день (`null`, см. `CalendarDay`) в цикл не
 * входит: первый клик по нему переводит его в `working`, как и клик по
 * дню с любым НЕИЗВЕСТНЫМ (вне трёх) значением — оба случая одинаково
 * "здесь ничего осмысленного не задано, начнём с рабочего". */
const DAY_TYPE_CYCLE: readonly KnownDayType[] = ["working", "weekend", "holiday"];

export function nextDayType(current: string | null): KnownDayType {
  const index = DAY_TYPE_CYCLE.indexOf(current as KnownDayType);
  if (index === -1) {
    return DAY_TYPE_CYCLE[0];
  }
  return DAY_TYPE_CYCLE[(index + 1) % DAY_TYPE_CYCLE.length];
}

/** Подпись типа дня для отображения — известное значение переводится,
 * неизвестное показывается как пришло (тот же приём, что и `WORK_SCHEDULE_
 * LABELS[type] ?? type` в `entities/employee.ts`), `null` — «не задан». */
export function dayTypeLabel(dayType: string | null): string {
  if (dayType === null) {
    return "Не задан";
  }
  return DAY_TYPE_LABELS[dayType as KnownDayType] ?? dayType;
}

export interface CalendarDay {
  /** ISO `YYYY-MM-DD`, как отдаёт и принимает сервер (`datetime.date` в
   * схеме) — формат для отображения (`ДД.ММ.ГГГГ`, `ui.md` §6) считает
   * `formatDate` (`app/format.ts`), здесь хранится исходный вид. */
  date: string;
  /** `null` — день ОТСУТСТВУЕТ в ответе сервера (см. докстринг экрана,
   * `ProductionCalendarPage.tsx`, — решение о том, что это означает и
   * как показывается, принято и записано там, не здесь: этот модуль
   * только строит и показывает данные, экран решает про их смысл). */
  dayType: string | null;
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** Все календарные даты года в порядке месяц-за-месяцем, день-за-днём —
 * `new Date(year, month + 1, 0).getDate()` даёт число дней в месяце
 * (день "0" следующего месяца — последний день текущего), учитывает
 * високосный год сам, без отдельной проверки. */
export function daysInYear(year: number): string[] {
  const dates: string[] = [];
  for (let month = 0; month < 12; month += 1) {
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    for (let day = 1; day <= daysInMonth; day += 1) {
      dates.push(`${year}-${pad2(month + 1)}-${pad2(day)}`);
    }
  }
  return dates;
}

/** Суббота/воскресенье по ISO-дате — используется только для «Заполнить
 * по умолчанию» (`T-M1-22`, коммит 4): это НЕ бизнес-правило раздела 5
 * требований и не подпадает под правило 5 `CLAUDE.md` — день недели
 * вычисляется из календаря, а не настраивается через `business_rules_
 * config`; правила про ПРАЗДНИКИ (которые настоящее бизнес-правило и
 * которые интерфейс сознательно не пытается угадывать) сюда не входят. */
export function isWeekendDate(isoDate: string): boolean {
  const [year, month, day] = isoDate.split("-").map(Number);
  const weekday = new Date(year, month - 1, day).getDay();
  return weekday === 0 || weekday === 6;
}

/** Собирает полную сетку года из ответа сервера: каждый календарный день
 * года получает свой `dayType` (или `null`, если сервер его не прислал —
 * см. `CalendarDay.dayType`). Сумма присутствующих (`dayType !== null`)
 * дней равна длине `serverDays` при любом входе — ни один день ответа не
 * теряется и не дублируется, потому что каждая дата года встречается в
 * `daysInYear` ровно один раз. */
export function buildYearGrid(
  year: number,
  serverDays: readonly ProductionCalendarDayOut[],
): CalendarDay[] {
  const typeByDate = new Map(serverDays.map((day) => [day.date, day.day_type]));
  return daysInYear(year).map((date) => ({
    date,
    dayType: typeByDate.get(date) ?? null,
  }));
}

/** «Заполнить по умолчанию» (`T-M1-22`, коммит 4) — трогает ТОЛЬКО дни с
 * `dayType === null`, у которых уже есть значение — не переопределяет:
 * это же свойство делает действие безопасным и для непустого (в т.ч.
 * частично заполненного из внешнего источника, `T-M8-03`) года, не
 * только для пустого — перезаписать готовый календарь одним нажатием
 * было бы опаснее, чем удобно, а заполнение только пробелов не может
 * стереть ничего, что уже было задано. */
export function fillDefaults(days: readonly CalendarDay[]): CalendarDay[] {
  return days.map((day) =>
    day.dayType === null
      ? { ...day, dayType: isWeekendDate(day.date) ? "weekend" : "working" }
      : day,
  );
}

export function hasUnsetDays(days: readonly CalendarDay[]): boolean {
  return days.some((day) => day.dayType === null);
}

/** Тело `PUT /api/admin/production-calendar/{year}` — ВЕСЬ год разом
 * (`T-M1-22` review-опасность 1, прецеденты `T-M1-52`/`T-M1-20`: неполное
 * тело молча стирает остальные дни). Вызывающая сторона обязана
 * проверить `!hasUnsetDays(days)` заранее — эта функция намеренно падает
 * на `null`, а не подставляет тип сама: подставлять что-то за
 * администратора значило бы придумывать данные календаря (правило 8), а
 * не собирать уже принятое решение в тело запроса. */
export function toDaysIn(days: readonly CalendarDay[]): ProductionCalendarDayIn[] {
  return days.map((day) => {
    if (day.dayType === null) {
      throw new Error(`${day.date}: тип дня не задан, нельзя включить в тело запроса`);
    }
    return { date: day.date, day_type: day.dayType as ProductionCalendarDayIn["day_type"] };
  });
}
