import type { components } from "../api/generated/schema";

export type BusinessRuleOut = components["schemas"]["BusinessRuleOut"];

/** Число полей для каркаса «Загрузки». ОЦЕНКА, не сверка: число ключей реестра приходит с
 * ответом `GET /api/admin/business-rules`, до загрузки его нет. Взято по числу ключей реестра на
 * момент `T-M2-15` (`backend/app/modules/business_rules/service.py`, `RULES`); при добавлении
 * ключа число не обязано меняться вместе с ним - оно не гарантирует равенства (`T-M2-24`). */
export const BUSINESS_RULES_FIELD_COUNT_ESTIMATE = 13;

/** Как вводить значение: по `value_type` из ответа, а не по имени ключа. Тип, которого клиент не
 * знает, вводится текстом как есть - решает сервер. */
export type RuleInputKind = "integer" | "text";

export function inputKindOf(rule: BusinessRuleOut): RuleInputKind {
  return rule.value_type === "integer" ? "integer" : "text";
}

/** Подпись границ под полем - только если сервер их прислал (`month_day` приходит без них). */
export function rangeHint(rule: BusinessRuleOut): string | null {
  if (rule.minimum === null || rule.maximum === null) {
    return null;
  }
  return `От ${rule.minimum} до ${rule.maximum}`;
}

/** Проверка до отправки: ТОЛЬКО то, что клиент знает из ответа - целое неотрицательное число в
 * границах `minimum`/`maximum`. Формат других типов (`month_day`) клиент не проверяет: его копия
 * была бы вторым местом без сторожа, отказ по нему приходит от сервера (`rulesErrorsByKey`).
 * `null` - значение годно (или проверка не наша). */
export function checkBeforeSend(rule: BusinessRuleOut, typed: string): string | null {
  if (inputKindOf(rule) !== "integer") {
    return null;
  }
  const hint = rangeHint(rule);
  if (!/^[0-9]+$/.test(typed)) {
    return hint ? `Нужно целое число. ${hint}.` : "Нужно целое число.";
  }
  const number = Number(typed);
  if (
    (rule.minimum !== null && number < rule.minimum) ||
    (rule.maximum !== null && number > rule.maximum)
  ) {
    return hint ? `${hint}.` : "Значение вне допустимого диапазона.";
  }
  return null;
}

/** Пары, которые ушли бы в `PUT`: только отличающиеся от значения на сервере. Сравнение
 * СТРОКОЙ, как пришло: `"028"` поверх `"28"` считается изменённым, и сервер сам решит, что пара
 * равна текущей (сравнивает по разобранному значению) и не запишет её. */
export function changedPairs(
  saved: readonly BusinessRuleOut[],
  typed: Readonly<Record<string, string | undefined>>,
): Record<string, string> {
  const pairs: Record<string, string> = {};
  for (const rule of saved) {
    const value = typed[rule.key];
    if (value !== undefined && value !== rule.value) {
      pairs[rule.key] = value;
    }
  }
  return pairs;
}

/** Значения полей формы по ответу сервера - ровно то, что он вернул (каноническая форма). */
export function valuesOf(rules: readonly BusinessRuleOut[]): Record<string, string> {
  return Object.fromEntries(rules.map((rule) => [rule.key, rule.value]));
}

/** Отказ 400 `PUT /api/admin/business-rules`: `detail` - одна строка `ключ: текст; ключ: текст`
 * со ВСЕМИ негодными ключами (`RuleValuesInvalidError`, `CLAUDE.md` «Соглашения»). Раскладывает
 * её по ключам, которые форма знает (пришли из `GET`). `null` - строка не в этой форме (или
 * в ней неизвестный ключ, или хвост без ключа): тогда её нельзя честно положить под поля, и
 * вызывающий показывает её целиком общим текстом. */
export function rulesErrorsByKey(
  detail: unknown,
  knownKeys: readonly string[],
): Record<string, string> | null {
  if (typeof detail !== "string" || knownKeys.length === 0) {
    return null;
  }
  const alternatives = [...knownKeys]
    .sort((a, b) => b.length - a.length)
    .map((key) => key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("|");
  const starts = [...detail.matchAll(new RegExp(`(?:^|; )(${alternatives}): `, "g"))];
  if (starts.length === 0 || starts[0].index !== 0) {
    return null;
  }
  const errors: Record<string, string> = {};
  starts.forEach((match, index) => {
    const textStart = match.index + match[0].length;
    const textEnd = index + 1 < starts.length ? starts[index + 1].index : detail.length;
    errors[match[1]] = detail.slice(textStart, textEnd);
  });
  return errors;
}
