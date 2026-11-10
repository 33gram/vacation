import type { components } from "../api/generated/schema";
import { formatDate } from "../app/format";
import type { Unit } from "./leaveType";

export type EmployeeBalances = components["schemas"]["EmployeeBalancesOut"];
export type LeaveTypeBalance = components["schemas"]["LeaveTypeBalanceOut"];
export type BalanceEntry = components["schemas"]["BalanceEntryOut"];
export type AccrualYear = components["schemas"]["AccrualYearOut"];

// ОДИН псевдоним `Unit` на фронтенде - из `leaveType.ts`: на бэкенде схема остатка берёт `Unit`
// из схемы вида, и направление то же. Расхождение полей `LeaveTypeBalanceOut.unit` и
// `LeaveTypeOut.unit` теперь ошибка компиляции там, где значение остатка попадает в `unitLabel`.
export type { Unit };

// Подпись единицы - ОДНО место для остатка (`FR-BAL-002`): `unit` приходит в ответе и у разных
// видов разный (у отгула сегодня рабочие дни, по `FR-DAYOFF-001` будут часы, `T-M3-31`), поэтому
// слово «дней» в разметке не пишется. Схема отдаёт `unit` перечислением (`T-M2-29`), поэтому
// `Record<Unit, string>` исчерпывающий: новая единица на бэкенде после перегенерации клиента
// останавливает `tsc`, а не доезжает до пользователя сырым кодом. Это СОКРАЩЕНИЯ для ячеек
// таблицы (`ui.md` §6); полные названия для формы вида - `UNIT_LABELS` в `leaveType.ts`, другой
// текст для другого места: не сводить в один.
const UNIT_LABELS: Record<Unit, string> = {
  calendar_days: "кал. дн.",
  working_days: "раб. дн.",
  hours: "ч",
};

// Запасное `?? unit` ОСТАВЛЕНО по тем же причинам, что у `entryTypeLabel`: клиент не проверяет
// тело ответа во время выполнения, и рассинхрон версий бэкенда и фронтенда пришлёт значение вне
// союза - лучше показать его как пришло, чем пустую ячейку.
export function unitLabel(unit: Unit): string {
  return UNIT_LABELS[unit] ?? unit;
}

export type EntryType = BalanceEntry["entry_type"];

// Подписи типов событий журнала (`models/leave_balance_entry.py`). Схема отдаёт `entry_type`
// перечислением (`T-M2-25`), поэтому `Record<EntryType, string>` исчерпывающий: новый тип на
// бэкенде после перегенерации клиента останавливает `tsc` ("Property ... is missing"), подпись
// для несуществующего типа - тоже ("Object literal may only specify known properties").
const ENTRY_TYPE_LABELS: Record<EntryType, string> = {
  accrual: "Начисление",
  usage: "Списание",
  usage_reversal: "Возврат при отмене",
  manual_adjustment: "Корректировка HR",
  recalculation: "Пересчёт",
  initial_import: "Начальный остаток",
  termination_closeout: "Фиксация при увольнении",
};

// Запасное `?? entryType` ОСТАВЛЕНО сознательно. Тип проверяет компилятор, а не ответ: клиент
// не проверяет тело ответа во время выполнения, и более новый бэкенд при выкатке (или кэш
// старой сборки фронтенда) пришлёт значение вне союза. Показать его как пришло лучше пустой
// ячейки. Это защита от рассинхрона версий, а не способ пропускать новые типы: новый тип в
// схеме без подписи сюда не доедет - сборка не пройдёт.
export function entryTypeLabel(entryType: EntryType): string {
  return ENTRY_TYPE_LABELS[entryType] ?? entryType;
}

/** Показ числа из ответа (`"18.67"`, `"-3.00"`) - ТОЛЬКО строковые операции: десятичная
 * запятая и отброшенные хвостовые нули. Арифметики над остатком на фронтенде нет и быть не
 * должно (`FR-BAL-002`: считает сервер). `signed` добавляет `+` к положительному. */
export function formatQuantity(value: string, { signed = false } = {}): string {
  let text = value.includes(".") ? value.replace(/0+$/, "").replace(/\.$/, "") : value;
  if (text === "-0" || text === "") {
    text = "0";
  }
  text = text.replace(".", ",");
  return signed && !text.startsWith("-") && text !== "0" ? `+${text}` : text;
}

export function formatAmount(value: string, unit: Unit, options?: { signed?: boolean }): string {
  return `${formatQuantity(value, options)} ${unitLabel(unit)}`;
}

/** Строка таблицы истории: событие журнала ИЛИ свёрнутые начисления рабочего года
 * (`T-M2-30`, `ui.md` §6). `folded` - признак второго; у него нет причины-события и автора:
 * год может содержать начисления с разными авторами (пересчёт несёт автора), поэтому в «Кто»
 * у свёрнутой строки прочерк, а подробности остаются в журнале. */
export interface HistoryRow {
  key: string;
  id: number | null; // `null` у свёрнутой строки: это агрегат, а не событие
  entry_date: string;
  entry_type: EntryType;
  quantity: string;
  reason: string | null;
  actor_name: string | null;
  folded: boolean;
}

/** Строки истории вида: события (`history`, без начислений) и свёрнутые начисления по годам
 * (`accrual_years`), СВЕЖИЕ СВЕРХУ: по дате по убыванию; при равной дате выше событие,
 * записанное позже (по `id`), а свёрнутая строка - ниже событий той же даты (начисление
 * записывается до корректировки, `FR-BAL-004`). Сортировка - не арифметика: даты сравниваются
 * как ISO-строки, количества не трогаются (итог года присылает сервер, `FR-BAL-002`). Порядок
 * ответа - по возрастанию - не меняется: сортируется здесь, в одном месте. */
export function historyRows(balance: LeaveTypeBalance): HistoryRow[] {
  const events: HistoryRow[] = balance.history.map((entry) => ({
    key: `event-${entry.id}`,
    id: entry.id,
    entry_date: entry.entry_date,
    entry_type: entry.entry_type,
    quantity: entry.quantity,
    reason: entry.reason,
    actor_name: entry.actor_name,
    folded: false,
  }));
  const folded: HistoryRow[] = balance.accrual_years.map((year) => ({
    key: `accrual-year-${year.working_year_start}`,
    id: null,
    entry_date: year.last_accrual_date,
    entry_type: "accrual",
    quantity: year.accrued,
    reason: `Начислено за рабочий год ${formatDate(year.working_year_start)} — ${formatDate(year.working_year_end)}`,
    actor_name: null,
    folded: true,
  }));
  return [...events, ...folded].sort((a, b) => {
    if (a.entry_date !== b.entry_date) {
      return a.entry_date < b.entry_date ? 1 : -1;
    }
    if (a.folded !== b.folded) {
      return a.folded ? 1 : -1;
    }
    return (b.id ?? 0) - (a.id ?? 0);
  });
}
