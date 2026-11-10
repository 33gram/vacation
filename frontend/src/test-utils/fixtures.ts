import type { components } from "../api/generated/schema";
import type { BusinessRuleOut } from "../entities/businessRules";
import type { LeaveTypeOut } from "../entities/leaveType";

type MeResponse = components["schemas"]["MeResponse"];
type EmployeeOut = components["schemas"]["EmployeeOut"];
type DepartmentOut = components["schemas"]["DepartmentOut"];
type LdapSettingsOut = components["schemas"]["LdapSettingsOut"];
type EmployeeBalancesOut = components["schemas"]["EmployeeBalancesOut"];
type LeaveTypeBalanceOut = components["schemas"]["LeaveTypeBalanceOut"];
type BalanceEntryOut = components["schemas"]["BalanceEntryOut"];
type AccrualYearOut = components["schemas"]["AccrualYearOut"];

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
    auth_type: "ldap",
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

export function ldapSettingsResponse(overrides: Partial<LdapSettingsOut> = {}): LdapSettingsOut {
  return {
    id: 1,
    server_url: "ldap://dc.company.local",
    port: 389,
    base_dn: "dc=company,dc=local",
    bind_dn: "cn=svc-vacation,dc=company,dc=local",
    user_match_attribute: "mail",
    is_active: true,
    last_tested_at: null,
    last_test_status: null,
    ...overrides,
  };
}

export function balanceEntryResponse(overrides: Partial<BalanceEntryOut> = {}): BalanceEntryOut {
  return {
    id: 1,
    entry_type: "accrual",
    quantity: "18.67",
    entry_date: "2026-10-24",
    working_year_start: "2026-03-10",
    working_year_end: "2027-03-09",
    reason: null,
    actor_name: "Система",
    ...overrides,
  };
}

export function accrualYearResponse(overrides: Partial<AccrualYearOut> = {}): AccrualYearOut {
  return {
    working_year_start: "2026-03-10",
    working_year_end: "2027-03-10",
    accrued: "18.67",
    last_accrual_date: "2026-10-24",
    ...overrides,
  };
}

export function leaveTypeBalanceResponse(
  overrides: Partial<LeaveTypeBalanceOut> = {},
): LeaveTypeBalanceOut {
  return {
    leave_type_id: 1,
    code: "annual_paid",
    name: "Ежегодный оплачиваемый отпуск",
    unit: "calendar_days",
    working_year_start: "2026-03-10",
    working_year_end: "2027-03-09",
    balance: "18.67",
    total_balance: "18.67",
    history: [balanceEntryResponse({ entry_type: "manual_adjustment" })],
    accrual_years: [],
    ...overrides,
  };
}

export function balancesResponse(
  balances: LeaveTypeBalanceOut[] = [leaveTypeBalanceResponse()],
  overrides: Partial<EmployeeBalancesOut> = {},
): EmployeeBalancesOut {
  return { employee_id: 7, as_of: "2026-10-26", balances, ...overrides };
}


export function leaveTypeResponse(overrides: Partial<LeaveTypeOut> = {}): LeaveTypeOut {
  return {
    id: 1,
    code: "annual_paid",
    name: "Основной оплачиваемый отпуск",
    is_paid: "full",
    unit: "calendar_days",
    reduces_main_balance: true,
    balance_tracked: true,
    requires_document: false,
    requires_approval: true,
    annual_limit: null,
    carry_over_policy: "unlimited",
    counted_non_working_days: "weekends_only",
    is_active: true,
    ...overrides,
  };
}

export function businessRuleResponse(overrides: Partial<BusinessRuleOut> = {}): BusinessRuleOut {
  return {
    key: "annual_leave_days",
    value: "28",
    value_type: "integer",
    description: "Длительность основного отпуска по умолчанию, календарных дней.",
    minimum: 1,
    maximum: 366,
    updated_at: "2026-10-20T09:30:00",
    updated_by_name: null,
    ...overrides,
  };
}
