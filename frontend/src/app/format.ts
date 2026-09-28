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
