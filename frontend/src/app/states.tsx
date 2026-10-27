import { Button, Result, Skeleton } from "antd";

// `ui.md` §4: пять состояний экрана, один компонент на каждое во всех
// разделах — не своя заглушка на каждом экране. Пятое состояние («Режим
// внедрения без LDAP») — отдельный компонент, `EmbeddedModeBanner.tsx`,
// он не про замену содержимого экрана, а про баннер поверх него.
//
// Иконки — встроенные статусы `antd.Result` (`error`/`info`/`403`), а не
// `@ant-design/icons`: пакет физически лежит в `node_modules` как
// транзитивная зависимость `antd`, но не объявлен в `package.json` —
// заводить его как прямую зависимость ради иконки не требуется задачей
// (T-M1-18: «новых зависимостей не добавлять»).

/** Форма каркаса «Загрузки» (`ui.md` §3: типы экранов, §4: «каркас строк той же высоты, что
 * и реальные данные»). ОБЯЗАТЕЛЬНЫЙ параметр `LoadingState`, без умолчания: новое место
 * вызова не может молча получить фиксированный каркас - компилятор заставит назвать форму.
 *   - `table` - «Список с фильтрами»: `rows` строк по `columns` ячеек;
 *   - `card` - «Карточка записи» и экраны из блоков: `blocks` блоков (заголовок и строки);
 *   - `form` - «Форма настроек»: `fields` полей (подпись и поле ввода). */
export type LoadingShape =
  | { kind: "table"; rows: number; columns: number }
  | { kind: "card"; blocks: number }
  | { kind: "form"; fields: number };

const range = (count: number) => Array.from({ length: count }, (_, index) => index);

/** «Загрузка» - каркас вместо крутилки (`ui.md` §4), по форме `shape`.
 *
 * ГАРАНТИРУЕТСЯ: число элементов каркаса (строк и ячеек, блоков, полей) равно тому, что назвал
 * вызывающий (`app/states.test.tsx`).
 * СВЕРЯЕТСЯ с тем, что страница рисует после загрузки, только там, где число известно ДО
 * загрузки: таблица сотрудников, формы сотрудника и LDAP, карточка сотрудника, календарь
 * (`app/loadingShapeParity.test.tsx`).
 * ОЦЕНКА, без сверки: где число зависит от данных или сессии - `DepartmentsPage`, `MyLeavePage`,
 * `PasswordPolicyChecklist`, `SessionGate`; в месте вызова каждого стоит комментарий «оценка»,
 * перечень и причины - `T-M2-24` в `docs/tasks.md`.
 * НЕ ПРОВЕРЯЕТСЯ: совпадение высоты в пикселях (в jsdom нет раскладки) и число строк, которое
 * зависит от данных, до загрузки неизвестных: у таблицы с постраничной выдачей каркас - полная
 * страница, у меньшего числа записей экран после загрузки будет ниже.
 *
 * Элементы помечены `data-skeleton-*` - по ним тесты считают каркас. */
export function LoadingState({ shape }: { shape: LoadingShape }) {
  if (shape.kind === "table") {
    return (
      <div aria-busy="true" data-skeleton="table">
        {range(shape.rows).map((row) => (
          <div key={row} data-skeleton-row style={{ display: "flex", gap: 16, marginBottom: 12 }}>
            {range(shape.columns).map((column) => (
              <div key={column} data-skeleton-cell style={{ flex: 1 }}>
                <Skeleton.Input active size="small" block />
              </div>
            ))}
          </div>
        ))}
      </div>
    );
  }
  if (shape.kind === "card") {
    return (
      <div aria-busy="true" data-skeleton="card">
        {range(shape.blocks).map((block) => (
          <div key={block} data-skeleton-block style={{ marginBottom: 24 }}>
            <Skeleton active title paragraph={{ rows: 3 }} />
          </div>
        ))}
      </div>
    );
  }
  return (
    <div aria-busy="true" data-skeleton="form">
      {range(shape.fields).map((field) => (
        <div key={field} data-skeleton-field style={{ marginBottom: 20 }}>
          <Skeleton.Input active size="small" style={{ width: 160, marginBottom: 8 }} />
          <Skeleton.Input active block />
        </div>
      ))}
    </div>
  );
}

/** «Ошибка загрузки» — технические подробности пользователю не показываются. */
export function ErrorLoadingState({ onRetry }: { onRetry: () => void }) {
  return (
    <Result
      status="error"
      title="Не удалось загрузить данные"
      subTitle="Попробуйте ещё раз."
      extra={
        <Button onClick={onRetry}>Повторить</Button>
      }
    />
  );
}

/** «Пусто» — текст различает «данных нет вообще» и «не найдено по
 * фильтрам»; кнопка основного действия — опциональна: там, где действовать
 * пока не на чем (например, страница-заглушка роли без доступных в M1
 * разделов, `ui.md` §2.3), кнопки нет вовсе — это не то же самое, что
 * «забыли добавить». */
export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <Result
      status="info"
      title={title}
      subTitle={description}
      extra={action ? <Button onClick={action.onClick}>{action.label}</Button> : undefined}
    />
  );
}

/** «Нет доступа к записи» — текст ровно тот, что вернул сервер, и кнопка
 * перехода туда, куда доступ есть. */
export function NoAccessState({
  message,
  onGoHome,
}: {
  message: string;
  onGoHome: () => void;
}) {
  return (
    <Result
      status="403"
      title={message}
      extra={<Button onClick={onGoHome}>На доступную страницу</Button>}
    />
  );
}
