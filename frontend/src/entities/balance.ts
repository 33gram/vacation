import type { components } from "../api/generated/schema";

export type EmployeeBalances = components["schemas"]["EmployeeBalancesOut"];
export type LeaveTypeBalance = components["schemas"]["LeaveTypeBalanceOut"];
export type BalanceEntry = components["schemas"]["BalanceEntryOut"];

// Подпись единицы - ОДНО место (`FR-BAL-002`): `unit` приходит в ответе и у разных видов
// разный (у отгула сегодня рабочие дни, по `FR-DAYOFF-001` будут часы, `T-M3-31`), поэтому
// слово «дней» в разметке не пишется. Значение, которого здесь нет, показывается как пришло.
const UNIT_LABELS: Record<string, string> = {
  calendar_days: "кал. дн.",
  working_days: "раб. дн.",
  hours: "ч",
};

export function unitLabel(unit: string): string {
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

export function formatAmount(value: string, unit: string, options?: { signed?: boolean }): string {
  return `${formatQuantity(value, options)} ${unitLabel(unit)}`;
}
