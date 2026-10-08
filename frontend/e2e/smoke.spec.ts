import { expect, test } from "@playwright/test";

// T-M1-24: единственная цель этого файла — доказать, что стек (nginx ->
// frontend/backend) поднят и отдаёт страницу входа, ДО того, как на этом
// основании будет писаться сам сценарий (ldap-login.spec.ts). Ничего
// функционального здесь не проверяется — только то, что инфраструктура
// работает, см. тело коммита за прогоном, который красен без поднятого
// стека.
test("login page loads and has login/password fields", async ({ page }) => {
  await page.goto("/login");

  await expect(page.getByLabel("Логин или e-mail")).toBeVisible();
  await expect(page.getByLabel("Пароль")).toBeVisible();
});
