import { Button, Input, Select, Space, Switch, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { formatDate } from "../app/format";
import { EmptyState, ErrorLoadingState, LoadingState } from "../app/states";
import { departmentNameById } from "../entities/department";
import type { DepartmentOut } from "../entities/department";
import {
  EMPLOYMENT_STATUS_LABELS,
  WORK_SCHEDULE_LABELS,
  employeeNameById,
  resolveName,
} from "../entities/employee";
import type { EmployeeOut } from "../entities/employee";
import { useDepartmentsQuery } from "../entities/useDepartmentsQuery";
import { useAllEmployeesQuery } from "../entities/useEmployeesQuery";

// Размер страницы выдачи - ОДНО число и для таблицы, и для каркаса загрузки (`ui.md` §4).
const EMPLOYEES_PAGE_SIZE = 10;

// Стабильные ссылки на пустой массив для состояния "данные ещё не пришли" —
// инлайновое `?? []` создавало бы новый массив на каждый рендер и срывало
// мемоизацию `useMemo` ниже (react-hooks/exhaustive-deps).
const NO_DEPARTMENTS: DepartmentOut[] = [];
const NO_EMPLOYEES: EmployeeOut[] = [];

/** «Список с фильтрами» (`ui.md` §3, `FR-EMP-001`). `GET /api/employees`
 * отдаёт ВЕСЬ видимый список одним ответом (`employee_service.
 * list_visible_employees`, `NFR-SCALE-001`) — запрашивается ВСЕГДА с
 * `include_terminated: true` (см. `useAllEmployeesQuery`), ФИО/табельный
 * номер, подразделение и «Показывать уволенных» фильтруются на уже
 * полученном списке, не повторным запросом. `include_terminated` на
 * сервере — чистое расширение фильтра (`list_visible_employees` сначала
 * решает видимость по роли и только потом применяет его), поэтому
 * запрашивать полный список безопасно для любой роли, включая
 * `employee`, который видит только себя и свою команду. */
export function EmployeesPage() {
  const navigate = useNavigate();
  const [showTerminated, setShowTerminated] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [departmentFilter, setDepartmentFilter] = useState<number | null>(null);

  const employeesQuery = useAllEmployeesQuery();
  const departmentsQuery = useDepartmentsQuery();

  const departments: DepartmentOut[] = departmentsQuery.data ?? NO_DEPARTMENTS;
  const employees: EmployeeOut[] = employeesQuery.data ?? NO_EMPLOYEES;

  const departmentNames = useMemo(() => departmentNameById(departments), [departments]);
  const managerNames = useMemo(() => employeeNameById(employees), [employees]);

  const filtersActive = searchText.trim() !== "" || departmentFilter !== null;

  const filteredEmployees = useMemo(() => {
    const normalizedSearch = searchText.trim().toLowerCase();
    return employees.filter((employee) => {
      if (!showTerminated && employee.employment_status === "terminated") {
        return false;
      }
      if (departmentFilter !== null && employee.department_id !== departmentFilter) {
        return false;
      }
      if (normalizedSearch === "") {
        return true;
      }
      return (
        employee.full_name.toLowerCase().includes(normalizedSearch) ||
        employee.personnel_number.toLowerCase().includes(normalizedSearch)
      );
    });
  }, [employees, searchText, departmentFilter, showTerminated]);

  // Сброс строки поиска/подразделения (ссылка «Сбросить» в строке
  // фильтров) — переключатель уволенных сюда не входит: он и так по
  // умолчанию выключен, включать его в ответ на очистку ПОИСКА было бы
  // неожиданным побочным эффектом для этого действия.
  const resetFilters = () => {
    setSearchText("");
    setDepartmentFilter(null);
  };

  // «Сбросить фильтры» в состоянии «Не найдено» — отдельное, более
  // сильное действие: единственный сценарий, где это состояние вообще
  // возможно при непустом `employees`, — весь показанный срез скрыт
  // текущими фильтрами, и переключатель уволенных здесь равноправная
  // тому причина, что и поиск/подразделение. Возврат «уволенных» к
  // умолчанию (`false`, то есть «не менять») ничего не исправил бы,
  // если весь штат уволен, — кнопка обязана снять именно то, что скрывает
  // данные, поэтому переключатель ставится в `true`, а не в исходное
  // значение.
  const resetAllFiltersFromEmptyState = () => {
    setSearchText("");
    setDepartmentFilter(null);
    setShowTerminated(true);
  };

  const columns: ColumnsType<EmployeeOut> = [
    {
      title: "ФИО",
      dataIndex: "full_name",
      key: "full_name",
      render: (fullName: string, employee) => <Link to={`/employees/${employee.id}`}>{fullName}</Link>,
    },
    {
      title: "Табельный номер",
      dataIndex: "personnel_number",
      key: "personnel_number",
      align: "right",
      render: (personnelNumber: string) => (
        <span style={{ fontVariantNumeric: "tabular-nums" }}>{personnelNumber}</span>
      ),
    },
    {
      title: "Подразделение",
      key: "department",
      render: (_, employee) => resolveName(employee.department_id, departmentNames),
    },
    {
      title: "Должность",
      dataIndex: "position",
      key: "position",
    },
    {
      title: "Дата приёма",
      dataIndex: "hire_date",
      key: "hire_date",
      render: (hireDate: string) => formatDate(hireDate),
    },
    {
      title: "Руководитель",
      key: "manager",
      render: (_, employee) => resolveName(employee.manager_id, managerNames),
    },
    {
      title: "График",
      dataIndex: "work_schedule_type",
      key: "work_schedule_type",
      render: (type: string) => WORK_SCHEDULE_LABELS[type] ?? type,
    },
    {
      title: "Статус",
      dataIndex: "employment_status",
      key: "employment_status",
      render: (status: string) => EMPLOYMENT_STATUS_LABELS[status] ?? status,
    },
  ];

  if (employeesQuery.isLoading || departmentsQuery.isLoading) {
    // Каркас по форме таблицы ниже: страница выдачи целиком и столько колонок, сколько в ней есть.
    return <LoadingState shape={{ kind: "table", rows: EMPLOYEES_PAGE_SIZE, columns: columns.length }} />;
  }
  if (employeesQuery.isError) {
    return <ErrorLoadingState onRetry={() => employeesQuery.refetch()} />;
  }
  if (departmentsQuery.isError) {
    return <ErrorLoadingState onRetry={() => departmentsQuery.refetch()} />;
  }

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Space align="center" style={{ width: "100%", justifyContent: "space-between" }}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          Сотрудники ({filteredEmployees.length})
        </Typography.Title>
        <Space>
          <Button onClick={() => navigate("/employees/import")}>Импорт</Button>
          <Button type="primary" onClick={() => navigate("/employees/new")}>
            Добавить сотрудника
          </Button>
        </Space>
      </Space>

      <Space wrap align="center" size="middle">
        <Input
          aria-label="ФИО или табельный номер"
          placeholder="ФИО или табельный номер"
          value={searchText}
          onChange={(event) => setSearchText(event.target.value)}
          style={{ width: 260 }}
          allowClear
        />
        <Select<number | null>
          aria-label="Подразделение"
          placeholder="Все подразделения"
          value={departmentFilter}
          onChange={(value) => setDepartmentFilter(value)}
          allowClear
          style={{ width: 220 }}
          options={departments.map((department) => ({ value: department.id, label: department.name }))}
        />
        <Space align="center">
          <Switch
            checked={showTerminated}
            onChange={setShowTerminated}
            aria-label="Показывать уволенных"
          />
          <Typography.Text>Показывать уволенных</Typography.Text>
        </Space>
        {filtersActive && (
          <Typography.Link onClick={resetFilters} role="link">
            Сбросить
          </Typography.Link>
        )}
      </Space>

      {employees.length === 0 ? (
        // Данных нет ВООБЩЕ — весь список (включая уволенных, т.к.
        // `employees` теперь всегда с `include_terminated: true`) пуст.
        <EmptyState
          title="Сотрудников нет"
          description="В системе пока не заведено ни одного сотрудника."
          action={{ label: "Добавить сотрудника", onClick: () => navigate("/employees/new") }}
        />
      ) : filteredEmployees.length === 0 ? (
        // Данные есть, но ни одна строка не проходит фильтры — включая
        // выключенный переключатель «Показывать уволенных» (если весь
        // штат уволен). Отдельный текст от «данных нет вообще» (ui.md
        // §4) и кнопка сброса ВСЕХ трёх фильтров, а не голый Alert.
        <EmptyState
          title="Не найдено"
          description="По заданным фильтрам ничего не найдено."
          action={{ label: "Сбросить фильтры", onClick: resetAllFiltersFromEmptyState }}
        />
      ) : (
        <Table<EmployeeOut>
          rowKey="id"
          columns={columns}
          dataSource={filteredEmployees}
          pagination={{ position: ["bottomCenter"], pageSize: EMPLOYEES_PAGE_SIZE }}
        />
      )}
    </Space>
  );
}
