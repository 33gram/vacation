import type { components } from "../api/generated/schema";

type MeResponse = components["schemas"]["MeResponse"];

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
