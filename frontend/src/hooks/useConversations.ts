import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/endpoints";
import type { ConversationDetail, FeedbackCreate } from "@/api/types";
import { qk } from "./keys";

export function useConversations() {
  return useQuery({ queryKey: qk.conversations, queryFn: () => api.conversations.list() });
}

export function useConversation(id: string | undefined, options: { pollMs?: number | false } = {}) {
  return useQuery({
    queryKey: qk.conversation(id ?? ""),
    queryFn: () => api.conversations.get(id as string),
    enabled: Boolean(id),
    refetchInterval: options.pollMs ?? false,
  });
}

export function useRenameConversation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => api.conversations.rename(id, title),
    onSuccess: (_c, { id }) => {
      void client.invalidateQueries({ queryKey: qk.conversations });
      void client.invalidateQueries({ queryKey: qk.conversation(id) });
    },
  });
}

export function useDeleteConversation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.conversations.remove(id),
    onSuccess: (_v, id) => {
      client.removeQueries({ queryKey: qk.conversation(id) });
      void client.invalidateQueries({ queryKey: qk.conversations });
      void client.invalidateQueries({ queryKey: qk.dashboard });
    },
  });
}

/** Submit thumbs up/down and reflect it in the cached conversation immediately. */
export function useSubmitFeedback(conversationId: string | undefined) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: FeedbackCreate) => api.feedback.submit(body),
    onSuccess: (out) => {
      if (!conversationId) return;
      client.setQueryData<ConversationDetail>(qk.conversation(conversationId), (prev) =>
        prev
          ? {
              ...prev,
              messages: prev.messages.map((m) => (m.id === out.message_id ? { ...m, feedback: out.rating } : m)),
            }
          : prev,
      );
    },
  });
}
