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

/** «Загрузка» — каркас той же формы, что и реальные данные, а не крутилка:
 * экран не должен «прыгать» после загрузки. */
export function LoadingState() {
  return <Skeleton active paragraph={{ rows: 4 }} />;
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
