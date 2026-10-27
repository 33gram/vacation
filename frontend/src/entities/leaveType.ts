import type { components } from "../api/generated/schema";

export type LeaveTypeOut = components["schemas"]["LeaveTypeOut"];
export type LeaveTypeCreate = components["schemas"]["LeaveTypeCreate"];
export type LeaveTypeUpdate = components["schemas"]["LeaveTypeUpdate"];

// Порядок вариантов в выпадающих списках формы задаётся ПОРЯДКОМ ОБЪЯВЛЕНИЯ подписей ниже
// (`optionsOf` берёт ключи по порядку): чтобы переставить варианты, переставьте подписи.
//
// Наборы значений берутся из ТИПОВ сгенерированного клиента (в схеме они `Literal`), а не
// перепечатываются списками вариантов: подписи заданы как `Record<значение, подпись>`, поэтому
// новое значение на бэкенде (после перегенерации клиента) остановит `tsc` на отсутствующей
// подписи, а не появится в интерфейсе молча сырым кодом.
export type IsPaid = LeaveTypeOut["is_paid"];
export type Unit = LeaveTypeOut["unit"];
export type CarryOverPolicy = LeaveTypeOut["carry_over_policy"];
export type CountedNonWorkingDays = LeaveTypeOut["counted_non_working_days"];

export const IS_PAID_LABELS: Record<IsPaid, string> = {
  full: "Оплачивается полностью",
  partial: "Оплачивается частично",
  none: "Не оплачивается",
};

export const UNIT_LABELS: Record<Unit, string> = {
  calendar_days: "Календарные дни",
  working_days: "Рабочие дни",
  hours: "Часы",
};

export const CARRY_OVER_LABELS: Record<CarryOverPolicy, string> = {
  none: "Не переносится",
  unlimited: "Переносится без ограничения",
};

export const COUNTED_NON_WORKING_DAYS_LABELS: Record<CountedNonWorkingDays, string> = {
  all: "Входят выходные и праздничные дни",
  weekends_only: "Входят выходные, праздничные не входят",
  none: "Не входят ни выходные, ни праздничные",
};

export function optionsOf<T extends string>(labels: Record<T, string>): { value: T; label: string }[] {
  return (Object.keys(labels) as T[]).map((value) => ({ value, label: labels[value] }));
}

export function yesNo(value: boolean): string {
  return value ? "Да" : "Нет";
}

// Подписи атрибутов вида (раздел 6 требований) - ОДИН источник для формы и карточки.
export const FIELD_LABELS = {
  code: "Код",
  name: "Название",
  is_paid: "Оплачиваемость",
  unit: "Единица учёта",
  reduces_main_balance: "Уменьшает остаток основного отпуска",
  balance_tracked: "По виду ведётся остаток",
  requires_document: "Требует подтверждающий документ",
  requires_approval: "Требует согласования",
  annual_limit: "Годовой лимит",
  carry_over_policy: "Перенос неиспользованного остатка",
  counted_non_working_days: "Выходные и праздники внутри периода",
  is_active: "Вид используется",
} as const;

// Размеры экранов для каркаса загрузки (`ui.md` §4); сверяются с отрисованным экраном тестом
// `app/loadingShapeParity.test.tsx`. У формы создания состояния «Загрузка» нет (данных она не
// грузит), поэтому числа полей для каркаса нет.
export const LEAVE_TYPES_PAGE_SIZE = 10;
// Число блоков карточки (`LeaveTypeCardPage`: «Основное» и «Правила использования») для каркаса
// загрузки: оторвано от разметки, сверяется тестом `loadingShapeParity.test.tsx` и меняется
// ВМЕСТЕ с карточкой.
export const LEAVE_TYPE_CARD_BLOCKS = 2;
