/** `ui.md` §6: даты — `ДД.ММ.ГГГГ`. Сервер отдаёт ISO (`YYYY-MM-DD`,
 * `datetime.date` в схеме) — единственное место, где формат меняется под
 * отображение, чтобы разбор строки не повторялся в каждой таблице/карточке. */
export function formatDate(isoDate: string | null): string {
  if (!isoDate) {
    return "—";
  }
  const [year, month, day] = isoDate.split("-");
  return `${day}.${month}.${year}`;
}

/** `ДД.ММ.ГГГГ ЧЧ:ММ` — расширение формата §6 временем, не замена: день
 * по-прежнему `formatDate`. `ui.md` §6 определяет формат только для
 * `datetime.date`; `LdapSettingsOut.last_tested_at` (`T-M1-21`) — пока
 * единственное поле `datetime.datetime` во всей схеме, и без времени две
 * проверки в один день было бы не различить. Разбор — по разделителю `T`
 * (стандартная ISO 8601 сериализация `datetime` в Pydantic v2/FastAPI),
 * не по фиксированным позициям символов: не проверено прогоном реального
 * ответа сервера (бэкенд в этой сессии не поднимался), и разбор по
 * разделителю устойчив к деталям хвоста строки (микросекунды, `Z` или
 * `+00:00`), которые позиционный разбор мог бы задеть. */
export function formatDateTime(isoDateTime: string | null): string {
  if (!isoDateTime) {
    return "—";
  }
  const [datePart, timePart] = isoDateTime.split("T");
  const formattedDate = formatDate(datePart);
  if (!timePart) {
    return formattedDate;
  }
  return `${formattedDate} ${timePart.slice(0, 5)}`;
}
