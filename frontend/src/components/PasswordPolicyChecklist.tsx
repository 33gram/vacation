import { List, Typography } from "antd";

import { usePasswordPolicyQuery } from "../app/passwordPolicy";
import { ErrorLoadingState, LoadingState } from "../app/states";

/** Чек-лист требований к паролю: клиент не вычисляет выполнение правил
 * сам и не хардкодит ни одного параметра политики (минимальная длина —
 * бизнес-правило, `T-M2-09`) — состав и формулировки приходят с сервера.
 * `violatedCodes` — коды из ответа 400 (`code`), подсвечиваются ВСЕ сразу,
 * а не первый (`ui.md` §7, `FR-AUTH-002`/`FR-EMP-001`). */
export function PasswordPolicyChecklist({
  violatedCodes,
}: {
  violatedCodes: Set<string> | null;
}) {
  const policyQuery = usePasswordPolicyQuery();

  if (policyQuery.isLoading) {
    return <LoadingState />;
  }
  if (policyQuery.isError) {
    return <ErrorLoadingState onRetry={() => policyQuery.refetch()} />;
  }
  if (!policyQuery.data) {
    return null;
  }

  return (
    <List
      size="small"
      header={<Typography.Text strong>Требования к паролю</Typography.Text>}
      dataSource={policyQuery.data}
      style={{ marginBottom: 24 }}
      renderItem={(hint) => {
        const violated = violatedCodes?.has(hint.code) ?? false;
        return (
          <List.Item>
            {/* Нарушение отмечается и текстом, не только цветом
                (type="danger") — красный сам по себе не годится ни для
                доступности (ui.md §9), ни для проверки в тестах "по
                подписи, не по структуре разметки". */}
            <Typography.Text type={violated ? "danger" : undefined}>
              {violated ? `${hint.hint} — не выполнено` : hint.hint}
            </Typography.Text>
          </List.Item>
        );
      }}
    />
  );
}
