import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Select, Space, Typography } from "antd";
import { useMemo, useState } from "react";

import { apiClient } from "../../api/client";
import { stringDetail } from "../../api/errors";
import { formatDate } from "../../app/format";
import { EmptyState, ErrorLoadingState, LoadingState } from "../../app/states";
import {
  buildYearGrid,
  dayTypeLabel,
  fillDefaults,
  hasUnsetDays,
  nextDayType,
  toDaysIn,
} from "../../entities/productionCalendar";
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

function DayCell({ day, onToggle }: { day: CalendarDay; onToggle: (date: string) => void }) {
  // `title` — атрибутивная метка типа (T-M1-22, коммит 2: «типы дней
  // различимы не только цветом»), а не только фон: цвет тестом не
  // проверить, и дальтонику он не поможет. Полная дата в подписи —
  // ДД.ММ.ГГГГ (`ui.md` §6), не голый ISO.
  const label = `${formatDate(day.date)} — ${dayTypeLabel(day.dayType)}`;
  return (
    <button
      type="button"
      title={label}
      onClick={() => onToggle(day.date)}
      style={{
        width: 28,
        height: 28,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 12,
        borderRadius: 4,
        border: "1px solid #d9d9d9",
        cursor: "pointer",
        background: backgroundFor(day.dayType),
      }}
    >
      {dayOfMonth(day.date)}
    </button>
  );
}

function MonthGrid({
  monthIndex,
  days,
  onToggleDay,
}: {
  monthIndex: number;
  days: CalendarDay[];
  onToggleDay: (date: string) => void;
}) {
  const monthDays = useMemo(
    () => days.filter((day) => monthIndexOf(day.date) === monthIndex),
    [days, monthIndex],
  );
  return (
    <div style={{ minWidth: 220 }}>
      <Typography.Text strong>{MONTH_NAMES[monthIndex]}</Typography.Text>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 8 }}>
        {monthDays.map((day) => (
          <DayCell key={day.date} day={day} onToggle={onToggleDay} />
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

/** Год как отдельный компонент, смонтированный с `key={year}`
 * (`ProductionCalendarPage` ниже) — переключение года ПОЛНОСТЬЮ
 * размонтирует и заново монтирует этот компонент, а не переиспользует
 * его состояние: локальная правка (`editedDays`) года, с которого ушли,
 * не должна ни утечь в другой год, ни требовать ручного сброса по
 * эффекту при смене `year`. Несохранённая правка при переключении года
 * теряется молча — в интерфейсе сегодня нигде нет предупреждения об
 * уходе с несохранённых изменений, вводить его только здесь не требуется
 * задачей. */
function ProductionCalendarYearView({ year }: { year: number }) {
  const queryClient = useQueryClient();
  const query = useProductionCalendarQuery(year);
  const [editedDays, setEditedDays] = useState<CalendarDay[] | null>(null);
  const [initialized, setInitialized] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const serverDays = query.data?.days ?? [];

  // Инициализация локальной копии ОДИН раз, как только год оказался НЕ
  // пустым, — до этого экран остаётся в состоянии «Пусто» (коммит 4
  // добавит туда действие «Заполнить по умолчанию», которое и заведёт
  // `editedDays` впервые для пустого года). `setState` прямо в теле
  // рендера (официально задокументированный в React паттерн «adjusting
  // state when data changes»), а не в `useEffect`: эффект сработал бы
  // ПОСЛЕ первой отрисовки с ещё `editedDays === null`, и год с уже
  // пришедшими днями на один кадр показал бы «Пусто» — здесь синхронно
  // в тот же проход рендера, без лишнего кадра. `initialized` — не
  // `editedDays !== null`, потому что сама последовательность условий
  // обязана сработать РОВНО один раз за смонтированный экземпляр (год
  // не меняется без полного размонтирования, см. докстринг выше), а не
  // при каждом рендере с пустым `editedDays`.
  if (!initialized && serverDays.length > 0) {
    setEditedDays(buildYearGrid(year, serverDays));
    setInitialized(true);
  }

  const saveMutation = useMutation({
    mutationFn: async (days: CalendarDay[]) => {
      const { error } = await apiClient.PUT("/api/admin/production-calendar/{year}", {
        params: { path: { year } },
        body: { days: toDaysIn(days) },
      });
      if (error) {
        throw new Error(stringDetail(error.detail) ?? "Не удалось сохранить календарь.");
      }
    },
    onSuccess: async () => {
      setSaveError(null);
      // Инвалидация, а не правка кэша по ответу `PUT` вслепую (T-M1-22
      // review-опасность 1 того же семейства, что и полная замена тела
      // запроса, — прецеденты `T-M1-52`/`T-M1-20`): подтверждённое
      // состояние обязано прийти отдельным `GET`, а не быть принятым на
      // веру из тела ответа мутации.
      await queryClient.invalidateQueries({ queryKey: ["production-calendar", year] });
    },
    onError: (error) => {
      setSaveError(error instanceof Error ? error.message : "Не удалось сохранить календарь.");
    },
  });

  if (query.isLoading) {
    return <LoadingState />;
  }
  if (query.isError) {
    return <ErrorLoadingState onRetry={() => query.refetch()} />;
  }

  // Общая для «Пусто» и для сетки: `current ?? buildYearGrid(year, serverDays)`
  // строит пустой скелет года, только если локальной копии ещё нет
  // (переход из «Пусто»), а если она уже есть (частично загруженный год,
  // уже, возможно, с правками администратора) — заполняет НЕЗАДАННЫЕ дни
  // прямо в ней, не перечитывая `serverDays` заново и не теряя эти правки.
  const handleFillDefaults = () => {
    setEditedDays((current) => fillDefaults(current ?? buildYearGrid(year, serverDays)));
  };

  if (editedDays === null) {
    return (
      <EmptyState
        title={`Календарь на ${year} год не заполнен`}
        description="Ни один день этого года ещё не загружен."
        action={{ label: "Заполнить по умолчанию", onClick: handleFillDefaults }}
      />
    );
  }

  const handleToggleDay = (date: string) => {
    setEditedDays((current) =>
      (current ?? []).map((day) =>
        day.date === date ? { ...day, dayType: nextDayType(day.dayType) } : day,
      ),
    );
  };

  const handleSave = () => {
    if (editedDays === null || hasUnsetDays(editedDays)) {
      return;
    }
    saveMutation.mutate(editedDays);
  };

  return (
    <div>
      {saveError && (
        <Alert type="error" message={saveError} showIcon style={{ marginBottom: 16 }} />
      )}

      <Space align="center" style={{ marginBottom: 16 }}>
        <Button
          type="primary"
          onClick={handleSave}
          loading={saveMutation.isPending}
          disabled={hasUnsetDays(editedDays)}
        >
          Сохранить
        </Button>
        {hasUnsetDays(editedDays) && (
          <Button onClick={handleFillDefaults}>Заполнить по умолчанию</Button>
        )}
        {hasUnsetDays(editedDays) && (
          <Typography.Text type="secondary">
            В году есть дни без заданного типа — сохранение недоступно, пока не заданы все.
          </Typography.Text>
        )}
      </Space>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 24 }}>
        {MONTH_NAMES.map((_, monthIndex) => (
          <MonthGrid
            key={monthIndex}
            monthIndex={monthIndex}
            days={editedDays}
            onToggleDay={handleToggleDay}
          />
        ))}
      </div>
    </div>
  );
}

/** Раздел «Администрирование → Производственный календарь» (`T-M1-22`,
 * `FR-ADMIN-003`). Устройство и состояния — `ui.md` §3 (абзац, добавленный
 * этой же веткой) — экран не подпадает ни под один из четырёх типов.
 * Доступ — только `system_admin` (`require_roles("system_admin")` на
 * ОБОИХ эндпоинтах бэкенда; `roles.ts` приведён в соответствие этой же
 * веткой — было ошибочно открыто и `hr_admin`).
 *
 * `GET` отвечает 200 с пустым `days`, если год ещё не загружен, — НЕ 404
 * (`production_calendar_service.get_year` — обычный `SELECT` по диапазону
 * дат года, отсутствие строк неотличимо от пустого результата обычного
 * запроса). Пустой массив — состояние «Пусто» (`ui.md` §4), а не пустая
 * сетка и не ошибка.
 *
 * День, ОТСУТСТВУЮЩИЙ в ответе сервера (`CalendarDay.dayType === null`,
 * `entities/productionCalendar.ts`), — решение принято здесь: этот экран
 * сам никогда не может породить такое состояние (сохранение всегда
 * отправляет ВЕСЬ год, ниже), но данные могут появиться и не через этот
 * экран (`T-M8-03`, прямой SQL) — частично заполненный год. Такой день
 * показывается отдельной, четвёртой меткой — «Не задан» (`dayTypeLabel`),
 * отличимой от всех трёх настоящих типов, а не молча подставленным
 * значением: подставить что-то самим значило бы придумать данные
 * календаря за администратора (правило 8). Такой день нельзя и сохранить
 * как есть — кнопка «Сохранить» недоступна, пока в году остаётся хотя бы
 * один незаданный день (`hasUnsetDays`), с пояснением почему; `PUT`
 * обязан получить тип для каждого дня года, а не гадать за администратора.
 *
 * Клик по дню переключает его тип по кругу (`nextDayType`) и правит
 * ТОЛЬКО локальное состояние — сохранение (`PUT`) отправляет ВЕСЬ год
 * разом (`toDaysIn(editedDays)`), а не изменённый день: `DepartmentUpdate`-
 * образная полная замена тела запроса, тот же риск, что стоил бы
 * сотрудникам паролей в `T-M1-52` и подразделениям — родителя и
 * руководителя в `T-M1-20`. Ошибка сервера показывается текстом сервера,
 * как есть, а не переписывается на клиенте (`ui.md` §7, «Тексты»), НАД
 * кнопками «Сохранить»/«Заполнить по умолчанию» — как и требует `ui.md`
 * §7, «Место ошибки», для ошибки всей операции (`PR #158` review:
 * изначально `Alert` был отрисован после блока кнопок, а не до).
 *
 * «Заполнить по умолчанию» (`fillDefaults`, `entities/productionCalendar.ts`)
 * — ТОЛЬКО суббота/воскресенье → `weekend`, остальное → `working`; это
 * вычисление из календаря (день недели), а не бизнес-правило раздела 5
 * требований, и потому не место ему в `business_rules_config` (правило 5
 * `CLAUDE.md`). Праздники ТАК не вычислить — они устанавливаются
 * постановлением правительства и переносятся год от года; их сюда не
 * подставляет ничто в этом экране ни в каком виде (ни список, ни правило
 * дат) — их вносит администратор вручную или приносит `T-M8-03`. Поэтому
 * результат действия — ОТПРАВНАЯ ТОЧКА для дальнейшей ручной правки, а не
 * готовый производственный календарь: сохранённый как есть год без единой
 * правки праздников был бы неверным.
 *
 * Действие доступно не только для пустого года, но и для частично
 * заполненного — `fillDefaults` трогает ТОЛЬКО дни с `dayType === null`
 * (`hasUnsetDays`), никогда не переписывая уже заданный тип, включая уже
 * внесённые здесь же, но ещё не сохранённые правки администратора. Поэтому
 * кнопка показана везде, где есть хоть один незаданный день (см. её рядом
 * с «Сохранить» ниже), и НИГДЕ не показана для года без единого незаданного
 * дня — заполненный (в том числе полностью готовый) год кнопки не увидит
 * вовсе, и перезаписать одним нажатием уже расставленные вручную праздники
 * этим действием нельзя: оно физически не умеет менять то, что уже задано,
 * а не просто «не предложено» интерфейсом. Отдельного действия «перезаписать
 * всё по умолчанию» эта задача не просит, и заводить его — значило бы дать
 * способ стереть готовый календарь одним кликом ради экономии, которая
 * при частичном заполнении и так уже есть через это же действие. */
export function ProductionCalendarPage() {
  const [year, setYear] = useState(CURRENT_YEAR);

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
      <ProductionCalendarYearView key={year} year={year} />
    </div>
  );
}
