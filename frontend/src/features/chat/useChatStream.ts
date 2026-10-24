import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useReducer, useRef } from "react";
import { isApiError } from "@/api/client";
import { streamChat } from "@/api/sse";
import type { ChatResponse, ConversationDetail, Evidence, Message } from "@/api/types";
import { qk } from "@/hooks/keys";
import { initialTurn, turnReducer } from "./streamReducer";

export const RECOVERY_TIMEOUT_MS = 120_000;

export interface SendArgs {
  question: string;
  conversationId: string | null;
  collectionIds: string[];
}

/** Merge a finished turn into the cached conversation so it renders without a refetch. */
export function mergeFinalIntoConversation(prev: ConversationDetail | undefined, question: string, final: ChatResponse): ConversationDetail {
  const userMessage: Message = {
    id: final.user_message_id,
    role: "user",
    content: question,
    citations: [],
    metadata: {},
    run_id: null,
    created_at: final.message.created_at,
    feedback: null,
  };
  const assistant: Message = {
    ...final.message,
    // keep evidence references on the message, like history does
    metadata: { ...final.message.metadata, evidence: final.evidence },
  };
  const now = final.message.created_at;
  const base: ConversationDetail = prev ?? {
    id: final.conversation_id,
    title: question.length > 80 ? `${question.slice(0, 77)}…` : question,
    collection_id: null,
    created_at: now,
    updated_at: now,
    last_message_at: now,
    message_count: 0,
    messages: [],
  };
  const existing = new Set(base.messages.map((m) => m.id));
  const messages = [...base.messages];
  if (!existing.has(userMessage.id)) messages.push(userMessage);
  if (!existing.has(assistant.id)) messages.push(assistant);
  return { ...base, messages, message_count: messages.length, last_message_at: now };
}

export function useChatStream(options: {
  onConversationId?: (id: string) => void;
  /** Live evidence (with passage text) for answers produced in this session. */
  onEvidence?: (messageId: string, evidence: Evidence[]) => void;
}) {
  const [turn, dispatch] = useReducer(turnReducer, initialTurn);
  const controller = useRef<AbortController | null>(null);
  const client = useQueryClient();
  const optionsRef = useRef(options);
  useEffect(() => {
    optionsRef.current = options;
  });

  useEffect(() => () => controller.current?.abort(), []);

  const send = useCallback(
    async ({ question, conversationId, collectionIds }: SendArgs) => {
      controller.current?.abort();
      const abort = new AbortController();
      controller.current = abort;
      dispatch({ type: "start", question, conversationId });
      let finished = false;
      try {
        await streamChat(
          { message: question, conversation_id: conversationId, collection_ids: collectionIds.length ? collectionIds : null },
          {
            signal: abort.signal,
            onEvent: (event) => {
              dispatch({ type: "event", event });
              if (event.event === "meta" && !conversationId) optionsRef.current.onConversationId?.(event.data.conversation_id);
              if (event.event === "final") {
                finished = true;
                const final = event.data;
                optionsRef.current.onEvidence?.(final.message.id, final.evidence);
                client.setQueryData<ConversationDetail>(qk.conversation(final.conversation_id), (prev) =>
                  mergeFinalIntoConversation(prev, question, final),
                );
                void client.invalidateQueries({ queryKey: qk.conversations });
                void client.invalidateQueries({ queryKey: qk.dashboard });
              }
              if (event.event === "error") finished = true;
            },
          },
        );
        if (!finished) dispatch({ type: "interrupted", reason: "network" });
      } catch (error) {
        if (abort.signal.aborted) {
          dispatch({ type: "interrupted", reason: "aborted" });
        } else if (isApiError(error) && error.status > 0) {
          dispatch({
            type: "failed",
            error: { code: error.code, message: error.message, requestId: error.requestId, retryAfter: error.retryAfter },
          });
        } else {
          // Connection dropped mid-stream: the server keeps running and persists the answer.
          dispatch({ type: "interrupted", reason: "network" });
        }
      } finally {
        if (controller.current === abort) controller.current = null;
        void client.invalidateQueries({ queryKey: qk.conversations });
      }
    },
    [client],
  );

  // Stop polling for a persisted answer after a while; the user can then ask again.
  useEffect(() => {
    if (turn.status !== "interrupted" || turn.recoveryExpired) return;
    const timer = setTimeout(() => dispatch({ type: "recovery_expired" }), RECOVERY_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [turn.status, turn.recoveryExpired]);

  const stop = useCallback(() => controller.current?.abort(), []);
  const clear = useCallback(() => dispatch({ type: "clear" }), []);

  return { turn, send, stop, clear };
}
