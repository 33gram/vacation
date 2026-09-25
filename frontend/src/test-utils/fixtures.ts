import type { components } from "../api/generated/schema";

type MeResponse = components["schemas"]["MeResponse"];
type EmployeeOut = components["schemas"]["EmployeeOut"];
type DepartmentOut = components["schemas"]["DepartmentOut"];

export function meResponse(overrides: Partial<MeResponse> = {}): MeResponse {
  return {
    id: 1,
    role: "employee",
    is_active: true,
    must_change_password: false,
    employee_id: 1,
    login: null,
    embedded_mode: false,
    ...overrides,
  };
}

// Пример состава — коды и русские подсказки реалистичны (соответствуют
// `password_policy.PASSWORD_POLICY_VIOLATION_CODES`), но тесты сверяют
// поведение экрана (что показывает, что подсвечивает), а не то, что это
// именно эти семь пунктов — контракт содержимого проверяет backend
// (`test_password_policy_codes_and_hints_are_the_same_set`).
export const PASSWORD_POLICY_HINTS = [
  { code: "too_short", hint: "Пароль должен быть не короче 8 символов." },
  { code: "insufficient_character_classes", hint: "Пароль должен содержать символы минимум трёх типов из четырёх." },
  { code: "common_password", hint: "Пароль входит в список распространённых паролей." },
];

export function employeeResponse(overrides: Partial<EmployeeOut> = {}): EmployeeOut {
  return {
    id: 1,
    full_name: "Иванов Иван Иванович",
    personnel_number: "1001",
    department_id: 1,
    position: "Инженер",
    hire_date: "2024-01-15",
    manager_id: null,
    email: "ivanov@company.local",
    work_schedule_type: "standard",
    employment_status: "active",
    termination_date: null,
    leave_category: null,
    ...overrides,
  };
}

export function departmentResponse(overrides: Partial<DepartmentOut> = {}): DepartmentOut {
  return {
    id: 1,
    name: "Отдел разработки",
    parent_id: null,
    head_employee_id: null,
    ...overrides,
  };
}
