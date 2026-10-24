import { describe, expect, it } from "vitest";
import type { ChatResponse, StreamEvent } from "@/api/types";
import { currentStepLabel, initialTurn, turnReducer, type TurnState } from "./streamReducer";

function finalResponse(content: string, outcome: "answered" | "abstained" = "answered"): ChatResponse {
  return {
    conversation_id: "c1",
    user_message_id: "u1",
    message: {
      id: "a1",
      role: "assistant",
      content,
      citations: [],
      metadata: { outcome, claims: [], provider: "mock", model: "mock-extractive", error: null },
      run_id: "r1",
      created_at: "2026-10-24T10:00:00Z",
      feedback: null,
    },
    run: { id: "r1", outcome, provider: "mock", model: "m", latency_ms: 10, retrieval_attempts: 1, llm_calls: 1, prompt_tokens: null, completion_tokens: null },
    evidence: [],
  };
}

function play(events: StreamEvent[], from: TurnState = turnReducer(initialTurn, { type: "start", question: "q?", conversationId: null })) {
  return events.reduce((s, event) => turnReducer(s, { type: "event", event }), from);
}

describe("turnReducer", () => {
  it("accumulates token deltas into a provisional draft and records meta ids", () => {
    const s = play([
      { event: "meta", data: { conversation_id: "c1", user_message_id: "u1" } },
      { event: "token", data: { text: "Remote work " } },
      { event: "token", data: { text: "is allowed [S1]." } },
    ]);
    expect(s.status).toBe("streaming");
    expect(s.draft).toBe("Remote work is allowed [S1].");
    expect(s.conversationId).toBe("c1");
    expect(s.userMessageId).toBe("u1");
  });

  it("token_reset discards the streamed draft so the regenerated answer starts clean", () => {
    const s = play([
      { event: "token", data: { text: "Bad answer [S9]" } },
      { event: "token_reset", data: {} },
      { event: "token", data: { text: "Good" } },
    ]);
    expect(s.draft).toBe("Good");
    expect(s.resets).toBe(1);
  });

  it("final replaces the streamed text with the validated answer (e.g. an abstention)", () => {
    const abstention = "I couldn't find enough evidence in your documents to answer that.";
    const s = play([
      { event: "token", data: { text: "Speculative answer without sources" } },
      { event: "final", data: finalResponse(abstention, "abstained") },
    ]);
    expect(s.status).toBe("done");
    expect(s.draft).toBe(abstention);
    expect(s.final?.message.metadata.outcome).toBe("abstained");
  });

  it("ignores late tokens after the final answer", () => {
    const s = play([
      { event: "final", data: finalResponse("Final.") },
      { event: "token", data: { text: " trailing" } },
    ]);
    expect(s.draft).toBe("Final.");
  });

  it("tracks step start/finish and exposes a live label", () => {
    const s = play([
      { event: "step", data: { node: "route", status: "started" } },
      { event: "step", data: { node: "route", status: "ok", latency_ms: 3 } },
      { event: "step", data: { node: "retrieve", status: "started" } },
    ]);
    expect(s.steps).toEqual([
      { node: "route", status: "ok", latency_ms: 3 },
      { node: "retrieve", status: "started" },
    ]);
    expect(currentStepLabel(s.steps)).toBe("Searching knowledge…");
  });

  it("an error event ends the turn with the server's message", () => {
    const s = play([{ event: "error", data: { code: "internal_error", message: "The answer could not be completed." } }]);
    expect(s.status).toBe("error");
    expect(s.error).toEqual({ code: "internal_error", message: "The answer could not be completed." });
  });

  it("interruption (stop / network) keeps the ids needed to recover the persisted answer", () => {
    const streaming = play([{ event: "meta", data: { conversation_id: "c1", user_message_id: "u1" } }]);
    const s = turnReducer(streaming, { type: "interrupted", reason: "aborted" });
    expect(s).toMatchObject({ status: "interrupted", interruption: "aborted", conversationId: "c1", userMessageId: "u1" });
    expect(turnReducer(s, { type: "recovery_expired" }).recoveryExpired).toBe(true);
  });
});
