import { expect, test } from "@playwright/test";

test("landing page loads with config from the backend", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Reels");
  await expect(page.getByRole("link", { name: "Try the simulator" })).toBeVisible(); // dev_mode came through the proxy
});

test("register, verify and reach the dashboard", async ({ page }) => {
  await page.goto("/register");
  await page.getByLabel("Name").fill("E2E Tester");
  await page.getByLabel("Email").fill(`e2e-${Date.now()}@example.com`);
  await page.getByLabel("Password").fill("correct horse battery");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Create account" }).click();
  await page.getByRole("link", { name: "Open the verification link" }).click(); // dev mode: no email is sent
  await expect(page.getByText(/verified/i).first()).toBeVisible();
  await page.goto("/dashboard");
  await expect(page.getByRole("link", { name: "Videos" })).toBeVisible();
});

test("simulator: sample footage to a finished reel", async ({ page }) => {
  await page.goto("/dev/whatsapp");
  await expect(page.getByRole("heading", { name: "WhatsApp simulator" })).toBeVisible();
  await page.getByTitle("Attach clips or photos").click();
  await page.getByRole("button", { name: "Send sample footage" }).click();
  await expect(page.getByRole("button", { name: "Send sample footage" })).toBeHidden({ timeout: 60_000 });

  await page.getByLabel("Message").fill("That's all. Make it a cinematic travel reel with a calm voiceover.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  const proceed = page.getByRole("button", { name: /proceed/i }).last();
  await expect(proceed).toBeVisible({ timeout: 60_000 });
  await proceed.click();

  await expect(page.getByText(/your reel is ready/i).first()).toBeVisible({ timeout: 200_000 });
});
