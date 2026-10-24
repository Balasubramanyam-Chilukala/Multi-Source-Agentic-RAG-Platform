import { expect, test } from "@playwright/test";

/**
 * Optional design-review screenshots (dark mode, 375px phone width). Runs only when
 * E2E_SCREENSHOTS points at an output directory; asserts there is no horizontal overflow.
 */
const shots = process.env.E2E_SCREENSHOTS;
test.skip(!shots, "set E2E_SCREENSHOTS to capture design screenshots");
test.use({ viewport: { width: 375, height: 812 }, colorScheme: "dark" });

test("mobile + dark mode render without horizontal overflow", async ({ page }) => {
  const stamp = Date.now();
  await page.goto("/register");
  await page.screenshot({ path: `${shots}/m-01-register.png`, fullPage: true });
  await page.getByLabel("Your name").fill("Mobile Tester");
  await page.getByLabel("Workspace").fill(`Mobile ${stamp}`);
  await page.getByLabel("Work email").fill(`mobile-${stamp}@example.com`);
  await page.getByLabel("Password").fill("e2e-password-123");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page.getByRole("heading", { name: "Workspace overview" })).toBeVisible();

  for (const [path, name] of [
    ["/", "m-02-dashboard"],
    ["/knowledge", "m-03-knowledge"],
    ["/chat", "m-04-chat"],
    ["/runs", "m-05-runs"],
    ["/evaluations", "m-06-evaluations"],
    ["/settings", "m-07-settings"],
  ] as const) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: `${shots}/${name}.png`, fullPage: true });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `horizontal overflow on ${path}`).toBeLessThanOrEqual(0);
  }

  await page.getByRole("button", { name: "Open menu" }).click();
  await page.screenshot({ path: `${shots}/m-08-menu.png` });
});
