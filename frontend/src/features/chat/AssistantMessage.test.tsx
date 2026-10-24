import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Message } from "@/api/types";
import { OutcomeBadge } from "@/components/badges";
import { mockFetch, renderPage, signIn } from "@/test/utils";
import { AssistantMessage } from "./AssistantMessage";

function message(overrides: Partial<Message> & { metadata?: Message["metadata"] }): Message {
  return {
    id: "a1",
    role: "assistant",
    content: "Answer [S1].",
    citations: [{ marker: "S1", chunk_id: "c1", document_id: "d1", document_title: "Doc" }],
    run_id: "run-1",
    created_at: "2026-10-24T10:00:00Z",
    feedback: null,
    ...overrides,
    metadata: { outcome: "answered", provider: "groq", model: "openai/gpt-oss-120b", claims: [], ...overrides.metadata },
  };
}

function renderMessage(m: Message, onRetry = vi.fn()) {
  renderPage(
    <AssistantMessage message={m} conversationId="conv" evidenceCount={1} selected={false} activeMarker={null} onOpenEvidence={() => {}} onRetry={onRetry} />,
  );
  return onRetry;
}

beforeEach(() => {
  signIn("member");
  mockFetch({ "GET /api/v1/auth/me": () => ({ user_id: "u", tenant_id: "t", email: "m@example.com", role: "member" }) });
});
afterEach(() => vi.unstubAllGlobals());

describe("outcome styling", () => {
  it("abstained answers get a distinct, honest 'Not enough evidence' treatment", async () => {
    renderMessage(message({ content: "I can't answer that from your documents.", citations: [], metadata: { outcome: "abstained" } }));
    expect(await screen.findByText("Not enough evidence.")).toBeInTheDocument();
    expect(screen.getByText("Not enough evidence", { selector: "[data-outcome] *, [data-outcome]" })).toBeInTheDocument();
    expect(screen.getByRole("article")).toHaveAttribute("data-outcome", "abstained");
  });

  it("error answers show the error message and a Retry button that resends", async () => {
    const onRetry = renderMessage(
      message({ content: "x", citations: [], metadata: { outcome: "error", error: { category: "rate_limited", message: "The AI provider is busy. Please retry." } } }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("The AI provider is busy. Please retry.");
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("OutcomeBadge maps every outcome to a label", () => {
    renderPage(
      <div>
        <OutcomeBadge outcome="answered" />
        <OutcomeBadge outcome="abstained" />
        <OutcomeBadge outcome="clarification" />
        <OutcomeBadge outcome="error" />
      </div>,
    );
    for (const label of ["Answered", "Not enough evidence", "Needs clarification", "Error"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("shows provider/model, and a demo badge only for the mock provider", async () => {
    renderMessage(message({}));
    expect(await screen.findByText("groq")).toBeInTheDocument();
    expect(screen.queryByText("demo / mock")).not.toBeInTheDocument();
  });
});

describe("feedback and trace", () => {
  it("thumbs up posts feedback and reflects the rating", async () => {
    const { calls } = mockFetch({
      "GET /api/v1/auth/me": () => ({ user_id: "u", tenant_id: "t", email: "m@example.com", role: "member" }),
      "POST /api/v1/feedback": () => ({ id: "f1", message_id: "a1", rating: 1, comment: null, created_at: "2026-10-24T10:00:00Z" }),
    });
    renderMessage(message({}));
    const up = await screen.findByRole("button", { name: "Thumbs up" });
    expect(up).toHaveAttribute("aria-pressed", "false");
    await userEvent.setup().click(up);
    expect(up).toHaveAttribute("aria-pressed", "true");
    expect(calls.find((c) => c.path === "/api/v1/feedback")?.body).toEqual({ message_id: "a1", rating: 1 });
  });

  it("links to the run trace", async () => {
    renderMessage(message({}));
    expect(await screen.findByRole("link", { name: "Open run trace" })).toHaveAttribute("href", "/runs/run-1");
  });

  it("toggle highlights uncited sentences as general context", async () => {
    renderMessage(
      message({
        content: "Cited fact [S1]. Generic advice here.",
        metadata: { outcome: "answered", claims: [{ text: "Cited fact .", supported: true, markers: ["S1"] }, { text: "Generic advice here.", supported: false, markers: [] }] },
      }),
    );
    expect(await screen.findByText("1/2 sentences cited")).toBeInTheDocument();
    expect(document.querySelector("mark.unsupported")).toBeNull();
    await userEvent.setup().click(screen.getByRole("switch", { name: /Highlight general context/ }));
    expect(document.querySelector(".prose-atlas mark.unsupported")).toHaveTextContent("Generic advice here.");
  });
});
