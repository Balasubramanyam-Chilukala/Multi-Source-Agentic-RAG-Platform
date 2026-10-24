/**
 * State of one in-flight chat turn, driven by SSE events. Kept pure so it is easy to test.
 *
 * Streamed tokens are provisional: `token_reset` discards the draft (the agent is regenerating)
 * and `final` replaces it with the validated answer, which may differ (citations removed,
 * or turned into an abstention).
 */
import type { ChatResponse, StreamEvent } from "@/api/types";

export interface AgentStep {
  node: string;
  status: "started" | "ok" | "error" | string;
  latency_ms?: number;
}

export type TurnStatus = "idle" | "streaming" | "done" | "error" | "interrupted";

export interface TurnError {
  code: string;
  message: string;
  requestId?: string | null;
  retryAfter?: number | null;
}

export interface TurnState {
  status: TurnStatus;
  question: string;
  conversationId: string | null;
  userMessageId: string | null;
  draft: string;
  /** Number of times the draft was discarded by token_reset. */
  resets: number;
  steps: AgentStep[];
  final: ChatResponse | null;
  error: TurnError | null;
  /** Why the stream ended early: user pressed stop, or the connection dropped. */
  interruption: "aborted" | "network" | null;
  /** After an interruption we poll for the persisted answer until this becomes true. */
  recoveryExpired: boolean;
}

export const initialTurn: TurnState = {
  status: "idle",
  question: "",
  conversationId: null,
  userMessageId: null,
  draft: "",
  resets: 0,
  steps: [],
  final: null,
  error: null,
  interruption: null,
  recoveryExpired: false,
};

export type TurnAction =
  | { type: "start"; question: string; conversationId: string | null }
  | { type: "event"; event: StreamEvent }
  | { type: "failed"; error: TurnError }
  | { type: "interrupted"; reason: "aborted" | "network" }
  | { type: "recovery_expired" }
  | { type: "clear" };

function applyStep(steps: AgentStep[], step: AgentStep): AgentStep[] {
  if (step.status === "started") return [...steps, step];
  // Completion: update the most recent "started" entry for this node.
  for (let i = steps.length - 1; i >= 0; i--) {
    const s = steps[i];
    if (s && s.node === step.node && s.status === "started") {
      const next = steps.slice();
      next[i] = step;
      return next;
    }
  }
  return [...steps, step];
}

export function turnReducer(state: TurnState, action: TurnAction): TurnState {
  switch (action.type) {
    case "start":
      return { ...initialTurn, status: "streaming", question: action.question, conversationId: action.conversationId };
    case "clear":
      return initialTurn;
    case "failed":
      return { ...state, status: "error", error: action.error };
    case "recovery_expired":
      return state.status === "interrupted" ? { ...state, recoveryExpired: true } : state;
    case "interrupted":
      return state.status === "streaming" ? { ...state, status: "interrupted", interruption: action.reason } : state;
    case "event": {
      if (state.status !== "streaming") return state; // ignore late events after stop/final
      const { event } = action;
      switch (event.event) {
        case "meta":
          return { ...state, conversationId: event.data.conversation_id, userMessageId: event.data.user_message_id };
        case "step":
          return { ...state, steps: applyStep(state.steps, event.data) };
        case "token":
          return { ...state, draft: state.draft + (event.data.text ?? "") };
        case "token_reset":
          return { ...state, draft: "", resets: state.resets + 1 };
        case "final":
          return {
            ...state,
            status: "done",
            final: event.data,
            draft: event.data.message.content, // the validated answer replaces the provisional draft
            conversationId: event.data.conversation_id,
            userMessageId: event.data.user_message_id,
          };
        case "error":
          return { ...state, status: "error", error: { code: event.data.code, message: event.data.message } };
        default:
          return state;
      }
    }
    default:
      return state;
  }
}

const STEP_LABELS: Record<string, string> = {
  validate_input: "Checking the question",
  route: "Planning",
  retrieve: "Searching knowledge",
  refine: "Refining the search",
  generate: "Generating answer",
  summarize: "Summarizing document",
  validate_answer: "Checking citations",
  abstain: "Assessing evidence",
  calculate: "Calculating",
  list_documents: "Listing documents",
  direct: "Answering",
  clarify: "Asking for clarification",
  finalize: "Finalizing",
  persist: "Saving",
};

export function stepLabel(node: string): string {
  return STEP_LABELS[node] ?? node.replace(/_/g, " ");
}

/** The label to show in the live indicator: the latest step still running, else the last one. */
export function currentStepLabel(steps: AgentStep[]): string {
  for (let i = steps.length - 1; i >= 0; i--) {
    const s = steps[i];
    if (s?.status === "started") return `${stepLabel(s.node)}…`;
  }
  return steps.length ? `${stepLabel(steps[steps.length - 1]?.node ?? "")}…` : "Starting…";
}
