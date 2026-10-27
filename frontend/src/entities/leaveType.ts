import type { components } from "../api/generated/schema";

export type LeaveTypeOut = components["schemas"]["LeaveTypeOut"];
export type LeaveTypeCreate = components["schemas"]["LeaveTypeCreate"];
export type LeaveTypeUpdate = components["schemas"]["LeaveTypeUpdate"];

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

export const YES_NO = (value: boolean): string => (value ? "Да" : "Нет");

// Размеры экранов для каркаса загрузки (`ui.md` §4); сверяются с отрисованным экраном тестом
// `app/loadingShapeParity.test.tsx`. Полей формы 12: код и все одиннадцать атрибутов раздела 6
// (в режиме правки код показывается, но не меняется).
export const LEAVE_TYPES_PAGE_SIZE = 10;
export const LEAVE_TYPE_FORM_FIELD_COUNT = 12;
export const LEAVE_TYPE_CARD_BLOCKS = 2;
