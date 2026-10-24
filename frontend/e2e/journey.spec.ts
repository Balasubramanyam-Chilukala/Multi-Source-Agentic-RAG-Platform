import { expect, test } from "@playwright/test";

const HANDBOOK = `# Atlas Remote Work Handbook

## Eligibility

Employees may work remotely up to three days per week after completing their probation period.
Managers must approve remote work schedules in the HR portal.

## Equipment

The company reimburses home office equipment up to 500 euros per year.
Receipts must be submitted within thirty days of purchase.
`;

const shots = process.env.E2E_SCREENSHOTS; // optional directory for design review screenshots

test("register → collection → upload → ready → streamed chat → citation → feedback → trace → settings", async ({ page }) => {
  const stamp = Date.now();
  const snap = async (name: string) => {
    if (shots) await page.screenshot({ path: `${shots}/${name}.png`, fullPage: true });
  };

  // Register a fresh workspace.
  await page.goto("/register");
  await page.getByLabel("Your name").fill("E2E Tester");
  await page.getByLabel("Workspace").fill(`E2E Workspace ${stamp}`);
  await page.getByLabel("Work email").fill(`e2e-${stamp}@example.com`);
  await page.getByLabel("Password").fill("e2e-password-123");
  await snap("01-register");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page.getByRole("heading", { name: "Workspace overview" })).toBeVisible();
  await snap("02-dashboard-empty");

  // Create a collection.
  await page.getByRole("link", { name: "Knowledge sources" }).first().click();
  await page.getByRole("button", { name: "New collection" }).first().click();
  await page.getByLabel("Name").fill("Handbook");
  await page.getByRole("button", { name: "Create collection" }).click();
  await expect(page.getByRole("heading", { name: "Handbook" })).toBeVisible();

  // Upload a Markdown file and watch ingestion finish (the list polls while pending/processing).
  await page.getByLabel("Choose files to upload").setInputFiles({ name: "remote-work.md", mimeType: "text/markdown", buffer: Buffer.from(HANDBOOK) });
  await expect(page.getByText("Uploaded — ingestion started")).toBeVisible();
  // The title is taken from the document's H1.
  const row = page.getByRole("row", { name: /Remote Work Handbook/ });
  await expect(row.getByText("Ready")).toBeVisible({ timeout: 60_000 });
  await snap("03-collection-ready");

  // Document drawer: chunks are listed.
  await expect(page.getByText("Chunks").locator("..").getByText("2")).toBeVisible(); // collection counts refresh
  await row.getByRole("button", { name: "Atlas Remote Work Handbook", exact: true }).click();
  await page.getByRole("tab", { name: /Chunks/ }).click();
  await expect(page.getByRole("dialog").getByText(/three days per week/).first()).toBeVisible();
  await snap("04-document-drawer");
  await page.keyboard.press("Escape");

  // Ask a question (streaming) scoped to this collection.
  await page.getByRole("link", { name: "Chat with this collection" }).click();
  await page.getByLabel("Ask a question about your documents").fill("How many days per week can employees work remotely?");
  await page.getByRole("button", { name: "Send message" }).click();
  const answer = page.getByRole("article", { name: "Assistant answer" }).last();
  await expect(answer).toBeVisible({ timeout: 60_000 });
  await expect(page).toHaveURL(/\/chat\/[0-9a-f-]{36}/);
  await expect(answer.getByText("demo / mock")).toBeVisible();
  await snap("05-chat-answer");

  // Click a citation chip → evidence panel shows the cited passage.
  const chip = answer.getByRole("button", { name: /^Source S\d+:/ }).first();
  await expect(chip).toBeVisible();
  await chip.click();
  const citation = page.getByRole("region", { name: /Citation S\d+/ });
  await expect(citation).toBeVisible();
  await expect(citation.getByText("Atlas Remote Work Handbook", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Retrieved passages" })).toBeVisible();
  await snap("06-evidence-panel");
  await page.getByRole("button", { name: "Close evidence panel" }).click();

  // Thumbs up is recorded.
  const up = answer.getByRole("button", { name: "Thumbs up" });
  await up.click();
  await expect(up).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("Thanks for the feedback")).toBeVisible();

  // Open the run trace.
  await answer.getByRole("link", { name: "Open run trace" }).click();
  await expect(page.getByRole("heading", { name: "Node timeline" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Node timeline" }).getByText("Searching knowledge")).toBeVisible();
  await expect(page.getByRole("region", { name: /Retrieval attempt 1/ })).toBeVisible();
  await snap("07-run-trace");

  // Settings: workspace vs. server configuration.
  await page.getByRole("link", { name: "Settings" }).first().click();
  await expect(page.getByRole("heading", { name: "Workspace settings" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Server configuration" })).toBeVisible();
  await expect(page.getByLabel("Top-k passages")).toBeEnabled(); // the registering user is the owner
  await snap("08-settings");

  // Evaluation: run retrieval-only evaluation for all modes and inspect the results.
  await page.getByRole("link", { name: "Evaluation" }).first().click();
  await page.getByRole("button", { name: "Run evaluation" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Run evaluation" });
  await expect(dialog.getByRole("switch")).toHaveAttribute("aria-checked", "false"); // answers off by default (quota)
  await dialog.getByRole("button", { name: "Start" }).click();
  await expect(page.getByText(/Queued \d evaluation run/)).toBeVisible();
  await expect(page.getByRole("img", { name: /Grouped bar chart/ })).toBeVisible({ timeout: 90_000 });
  await expect(page.getByRole("cell", { name: "Hybrid rerank" }).first()).toBeVisible();
  await snap("10-evaluation");
  await page.getByRole("link", { name: "Hybrid", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "By category" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Cases \(\d+\)/ })).toBeVisible();
  await snap("11-evaluation-run");

  // Dashboard reflects the activity; reload keeps the session.
  await page.getByRole("link", { name: "Dashboard" }).first().click();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Workspace overview" })).toBeVisible();
  await expect(page.getByRole("link", { name: /How many days per week/ })).toBeVisible();
  await snap("09-dashboard");
});
