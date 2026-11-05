import { useQueryClient } from "@tanstack/react-query";
import { Alert, Button, List, Table, Typography, Upload } from "antd";
import type { UploadFile } from "antd";
import { useState } from "react";

import { apiClient } from "../api/client";
import { detailText } from "../api/errors";
import type { components } from "../api/generated/schema";

type ImportSummary = components["schemas"]["ImportSummary"];

const FORMAT_COLUMNS = [
  "full_name",
  "personnel_number",
  "department",
  "position",
  "hire_date",
  "email",
  "work_schedule_type",
  "manager_personnel_number",
  "leave_category",
];

/** Импорт сотрудников (`FR-EMP-003`) — двухшаговый мастер: у
 * `POST /api/employees/import` нет шага предпросмотра (решение
 * `docs/ui.md` v1.3, первый коммит этой ветки) — файл отправляется, и
 * ответ сразу содержит итог: сколько строк принято и построчные причины
 * отказа для остальных, без промежуточного «сейчас проверим и покажем,
 * что получится». Возврат с шага отчёта на шаг выбора файла НЕ теряет
 * уже выбранный файл — `selectedFile` живёт в состоянии страницы, а не
 * шага. */
export function EmployeeImportPage() {
  const queryClient = useQueryClient();
  const [step, setStep] = useState<"select" | "report">("select");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const [generalError, setGeneralError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  const handleUpload = async () => {
    if (!selectedFile) {
      return;
    }
    setGeneralError(null);
    setUploading(true);

    const formData = new FormData();
    formData.append("file", selectedFile);
    // Схема объявляет тело как объект `{file}`, а не `FormData` — клиент
    // сериализует multipart сам (openapi-fetch не строит `FormData` из
    // обычного объекта автоматически), отсюда приведение типа ниже.
    const { data, error, response } = await apiClient.POST("/api/employees/import", {
      body: formData as never,
    });
    setUploading(false);

    if (!data) {
      // `responses` эндпоинта в схеме объявляет только 200/422 — файл,
      // отклонённый целиком (не тот формат, нет обязательной колонки),
      // приходит 400 и в схему не попал (тот же случай, что и
      // недокументированные 401 в `api/errors.ts`) — читаем тело
      // защитно, а не полагаемся на типизацию.
      const detail = (error as { detail?: unknown } | undefined)?.detail;
      setGeneralError(
        detailText(detail) ?? `Не удалось загрузить файл (код ${response.status}).`,
      );
      return;
    }

    setSummary(data);
    setStep("report");
    await queryClient.invalidateQueries({ queryKey: ["employees"] });
  };

  if (step === "report" && summary) {
    return (
      <div>
        <Typography.Title level={3}>Отчёт об импорте</Typography.Title>
        <List
          size="small"
          bordered
          style={{ maxWidth: 480, marginBottom: 24 }}
          dataSource={[
            { label: "Принято строк", value: summary.accepted_count },
            { label: "Отклонено строк", value: summary.rejected_count },
          ]}
          renderItem={(item) => (
            <List.Item>
              {item.label}: {item.value}
            </List.Item>
          )}
        />
        {summary.errors.length > 0 && (
          <Table
            rowKey={(row) => `${row.row_number}-${row.field}`}
            dataSource={summary.errors}
            pagination={false}
            columns={[
              { title: "Строка", dataIndex: "row_number", align: "right", width: 100 },
              { title: "Поле", dataIndex: "field" },
              { title: "Причина", dataIndex: "reason" },
            ]}
          />
        )}
        <Button style={{ marginTop: 24 }} onClick={() => setStep("select")}>
          Назад
        </Button>
      </div>
    );
  }

  return (
    <div>
      <Typography.Title level={3}>Импорт сотрудников</Typography.Title>
      <Typography.Paragraph>
        Файл CSV или XLSX с заголовком, содержащим колонки:{" "}
        <Typography.Text code>{FORMAT_COLUMNS.join(", ")}</Typography.Text>. Подразделение —
        по названию, руководитель — по табельному номеру (пусто только для вершины иерархии).
        Дата приёма — в формате ГГГГ-ММ-ДД. Категория для расчёта может быть пустой.
      </Typography.Paragraph>
      <Typography.Paragraph type="secondary">
        Предпросмотра нет: корректные строки записываются сразу, отклонённые — перечисляются в
        отчёте после загрузки.
      </Typography.Paragraph>

      <Upload
        beforeUpload={(file) => {
          setSelectedFile(file);
          return false;
        }}
        onRemove={() => setSelectedFile(null)}
        maxCount={1}
        fileList={
          selectedFile
            ? [{ uid: "selected-file", name: selectedFile.name, status: "done" } as UploadFile]
            : []
        }
      >
        <Button>Выбрать файл</Button>
      </Upload>

      {generalError && (
        <Alert type="error" message={generalError} showIcon style={{ marginTop: 16, maxWidth: 480 }} />
      )}

      <Button
        type="primary"
        style={{ marginTop: 24 }}
        disabled={!selectedFile}
        loading={uploading}
        onClick={handleUpload}
      >
        Загрузить
      </Button>
    </div>
  );
}
