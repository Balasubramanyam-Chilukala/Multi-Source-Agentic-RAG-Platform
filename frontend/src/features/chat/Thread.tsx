import type { Evidence, Message } from "@/api/types";
import { AssistantMessage } from "./AssistantMessage";

export function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-4 py-2.5 text-sm whitespace-pre-wrap text-primary-foreground" aria-label="Your question">
        {content}
      </div>
    </div>
  );
}

export function Thread({
  messages,
  conversationId,
  selection,
  evidenceFor,
  onSelect,
  onResend,
}: {
  messages: Message[];
  conversationId: string | undefined;
  selection: { messageId: string; marker: string | null } | null;
  evidenceFor: (m: Message) => Evidence[];
  onSelect: (s: { messageId: string; marker: string | null }) => void;
  onResend: (question: string) => void;
}) {
  return (
    <ol className="space-y-5" aria-label="Messages">
      {messages.map((m, i) => {
        if (m.role === "user") {
          return (
            <li key={m.id}>
              <UserBubble content={m.content} />
            </li>
          );
        }
        // The question that produced this answer, for the error Retry button.
        const question = messages
          .slice(0, i)
          .reverse()
          .find((p) => p.role === "user")?.content;
        return (
          <li key={m.id}>
            <AssistantMessage
              message={m}
              conversationId={conversationId}
              evidenceCount={evidenceFor(m).length}
              selected={selection?.messageId === m.id}
              activeMarker={selection?.messageId === m.id ? selection.marker : null}
              onOpenEvidence={(marker) => onSelect({ messageId: m.id, marker })}
              onRetry={question ? () => onResend(question) : undefined}
            />
          </li>
        );
      })}
    </ol>
  );
}
