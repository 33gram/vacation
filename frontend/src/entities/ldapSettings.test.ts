import { describe, expect, it } from "vitest";

import type { LdapSettingsOut } from "./ldapSettings";
import {
  DEFAULT_LDAP_PORT,
  DEFAULT_USER_MATCH_ATTRIBUTE,
  defaultFormValues,
  formValuesFromSaved,
  hasUnsavedChanges,
  lastTestStatusLabel,
  toLdapSettingsIn,
  toLdapTestRequest,
} from "./ldapSettings";

function savedFixture(overrides: Partial<LdapSettingsOut> = {}): LdapSettingsOut {
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

describe("defaultFormValues/formValuesFromSaved", () => {
  it("starts a brand-new form with the same defaults as the backend schema", () => {
    expect(defaultFormValues()).toEqual({
      server_url: "",
      port: DEFAULT_LDAP_PORT,
      base_dn: "",
      bind_dn: "",
      bind_password: "",
      user_match_attribute: DEFAULT_USER_MATCH_ATTRIBUTE,
      is_active: false,
    });
  });

  it("builds form values from a saved record with the password field always empty", () => {
    const saved = savedFixture();
    expect(formValuesFromSaved(saved)).toEqual({
      server_url: saved.server_url,
      port: saved.port,
      base_dn: saved.base_dn,
      bind_dn: saved.bind_dn,
      bind_password: "",
      user_match_attribute: saved.user_match_attribute,
      is_active: saved.is_active,
    });
  });
});

describe("lastTestStatusLabel", () => {
  it("labels the three known states", () => {
    expect(lastTestStatusLabel(null)).toBe("Ещё не проверялось");
    expect(lastTestStatusLabel("success")).toBe("Успешно");
    expect(lastTestStatusLabel("failure")).toBe("Неуспешно");
  });

  it("passes an unknown status through as-is rather than hiding it", () => {
    expect(lastTestStatusLabel("weird")).toBe("weird");
  });
});

describe("hasUnsavedChanges", () => {
  it("is true when nothing is saved yet, regardless of form contents", () => {
    expect(hasUnsavedChanges(defaultFormValues(), null)).toBe(true);
  });

  it("is false when the form exactly matches the saved record and the password field is empty", () => {
    const saved = savedFixture();
    expect(hasUnsavedChanges(formValuesFromSaved(saved), saved)).toBe(false);
  });

  it("is true when any non-password field differs from the saved record", () => {
    const saved = savedFixture();
    const values = { ...formValuesFromSaved(saved), port: 636 };
    expect(hasUnsavedChanges(values, saved)).toBe(true);
  });

  it("is true whenever the password field is non-empty, even if every other field matches", () => {
    const saved = savedFixture();
    const values = { ...formValuesFromSaved(saved), bind_password: "new-secret" };
    expect(hasUnsavedChanges(values, saved)).toBe(true);
  });
});

describe("toLdapSettingsIn", () => {
  it("omits bind_password entirely when the field is empty", () => {
    const values = { ...defaultFormValues(), server_url: "ldap://dc", base_dn: "dc=x", bind_dn: "cn=svc" };
    const body = toLdapSettingsIn(values);
    expect("bind_password" in body).toBe(false);
  });

  // Отрицательный контроль: если бы функция отправляла пустую строку, а
  // не опускала ключ, эта проверка бы упала — `"bind_password" in body`
  // было бы true для body.bind_password === "".
  it("negative control: an empty string would be caught as a present key", () => {
    const bodyWithEmptyString = { bind_password: "" };
    expect("bind_password" in bodyWithEmptyString).toBe(true);
  });

  it("includes bind_password when the field is filled", () => {
    const values = {
      ...defaultFormValues(),
      server_url: "ldap://dc",
      base_dn: "dc=x",
      bind_dn: "cn=svc",
      bind_password: "secret",
    };
    const body = toLdapSettingsIn(values);
    expect(body.bind_password).toBe("secret");
  });
});

describe("toLdapTestRequest", () => {
  it("sends an empty body when the form has no unsaved changes", () => {
    expect(toLdapTestRequest(defaultFormValues(), false)).toEqual({});
  });

  it("sends the filled draft fields, omitting bind_password when empty", () => {
    const values = {
      server_url: "ldap://dc",
      port: 389,
      base_dn: "dc=x",
      bind_dn: "cn=svc",
      bind_password: "",
      user_match_attribute: "mail",
      is_active: true,
    };
    const body = toLdapTestRequest(values, true);
    expect("bind_password" in body).toBe(false);
    expect(body).toEqual({
      server_url: "ldap://dc",
      port: 389,
      base_dn: "dc=x",
      bind_dn: "cn=svc",
      user_match_attribute: "mail",
    });
  });

  it("includes bind_password in the draft body when the field is filled", () => {
    const values = { ...defaultFormValues(), bind_password: "secret" };
    const body = toLdapTestRequest(values, true);
    expect(body.bind_password).toBe("secret");
  });
});
