import { PanelLeft, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";
import type { Evidence, Message } from "@/api/types";
import { ErrorState, LoadingRows } from "@/components/feedback";
import { Button } from "@/components/ui/button";
import { Dialog, SheetContent } from "@/components/ui/overlays";
import { useConversation } from "@/hooks/useConversations";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { Composer } from "./Composer";
import { ConversationSidebar } from "./ConversationSidebar";
import { EvidencePanel } from "./EvidencePanel";
import { PendingTurn } from "./PendingTurn";
import { ScopePicker } from "./ScopePicker";
import { Thread } from "./Thread";
import { useChatStream } from "./useChatStream";

const RECOVERY_POLL_MS = 2000;

const EXAMPLES = ["What documents are in this workspace?", "Summarize the key points of our handbook", "What does the policy say about remote work?"];

/** Has the server saved an assistant reply after the given user message? */
function hasReplyAfter(messages: Message[] | undefined, userMessageId: string | null): boolean {
  if (!messages || !userMessageId) return false;
  const index = messages.findIndex((m) => m.id === userMessageId);
  return index >= 0 && messages.slice(index + 1).some((m) => m.role === "assistant");
}

export default function ChatPage() {
  const { conversationId } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const wide = useMediaQuery("(min-width: 1280px)");
  const [liveEvidence, setLiveEvidence] = useState<Record<string, Evidence[]>>({});
  const [selection, setSelection] = useState<{ messageId: string; marker: string | null } | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  // null = not chosen by the user yet: fall back to ?collections= or the conversation's collection.
  const [scopeChoice, setScope] = useState<string[] | null>(() => params.get("collections")?.split(",").filter(Boolean) ?? null);

  const { turn, send, stop } = useChatStream({
    onConversationId: (id) => navigate(`/chat/${id}`, { replace: true }),
    onEvidence: (messageId, evidence) => setLiveEvidence((prev) => ({ ...prev, [messageId]: evidence })),
  });

  // After a stop / dropped connection the server still persists the answer: poll for it.
  const belongsHere = (turn.conversationId ?? null) === (conversationId ?? null);
  const awaitingServer = turn.status === "interrupted" && Boolean(turn.conversationId) && !turn.recoveryExpired;
  const conversation = useConversation(conversationId, { pollMs: awaitingServer && belongsHere ? RECOVERY_POLL_MS : false });
  const messages = useMemo(() => conversation.data?.messages ?? [], [conversation.data]);
  const recovered = turn.status === "interrupted" && hasReplyAfter(messages, turn.userMessageId);

  const conversationScope = conversation.data?.collection_id ? [conversation.data.collection_id] : [];
  const scope = scopeChoice ?? conversationScope;

  const sendQuestion = useCallback(
    (question: string) => {
      setSelection(null);
      void send({ question, conversationId: conversationId ?? null, collectionIds: scope });
    },
    [send, conversationId, scope],
  );

  const evidenceFor = useCallback((m: Message) => liveEvidence[m.id] ?? m.metadata.evidence ?? [], [liveEvidence]);
  // A selection from another conversation simply isn't found here.
  const selectedMessage = selection ? messages.find((m) => m.id === selection.messageId) : undefined;
  const showPending =
    belongsHere && !recovered && (turn.status === "streaming" || turn.status === "error" || turn.status === "interrupted");
  const visibleMessages = showPending && turn.userMessageId ? messages.filter((m) => m.id !== turn.userMessageId) : messages;
  const isEmpty = !conversationId && !showPending;

  const bottomRef = useRef<HTMLDivElement>(null);
  const hasDraft = turn.draft.length > 0;
  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ block: "end" });
  }, [visibleMessages.length, turn.status, hasDraft]);

  const panel = selectedMessage ? (
    <EvidencePanel
      message={selectedMessage}
      evidence={evidenceFor(selectedMessage)}
      activeMarker={selection?.marker ?? null}
      onClose={() => setSelection(null)}
    />
  ) : null;

  return (
    <div className="flex h-[calc(100dvh-3rem)] min-h-0 lg:h-dvh">
      <aside className="hidden w-64 shrink-0 border-r border-border bg-card md:block">
        <ConversationSidebar activeId={conversationId} />
      </aside>

      <section className="flex min-w-0 flex-1 flex-col" aria-label="Chat">
        <div className="flex items-center gap-2 border-b border-border px-4 py-2">
          <Button variant="ghost" size="icon-sm" className="md:hidden" aria-label="Show conversations" onClick={() => setHistoryOpen(true)}>
            <PanelLeft />
          </Button>
          <h1 className="min-w-0 flex-1 truncate text-sm font-semibold">{conversation.data?.title ?? (conversationId ? "Conversation" : "New conversation")}</h1>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-3xl space-y-5 px-4 py-6">
            {isEmpty ? (
              <div className="flex flex-col items-center gap-4 py-12 text-center">
                <div className="rounded-full bg-accent p-3 text-accent-foreground">
                  <Sparkles className="size-5" aria-hidden />
                </div>
                <div className="space-y-1">
                  <h2 className="text-lg font-semibold">Ask your knowledge base</h2>
                  <p className="max-w-md text-sm text-muted-foreground">
                    Answers cite the passages they come from. If the evidence isn't there, Atlas will tell you instead of guessing.
                  </p>
                </div>
                <div className="flex flex-wrap justify-center gap-2">
                  {EXAMPLES.map((q) => (
                    <Button key={q} variant="outline" size="sm" onClick={() => sendQuestion(q)}>
                      {q}
                    </Button>
                  ))}
                </div>
              </div>
            ) : conversation.isPending && conversationId && !showPending ? (
              <LoadingRows rows={3} />
            ) : conversation.error && !showPending ? (
              <ErrorState error={conversation.error} onRetry={() => void conversation.refetch()} />
            ) : (
              <Thread
                messages={visibleMessages}
                conversationId={conversationId}
                selection={selection}
                evidenceFor={evidenceFor}
                onSelect={setSelection}
                onResend={sendQuestion}
              />
            )}
            {showPending && <PendingTurn turn={turn} recovering={awaitingServer} onRetry={() => sendQuestion(turn.question)} />}
            <div ref={bottomRef} />
          </div>
        </div>

        <div className="border-t border-border bg-background px-4 py-3">
          <div className="mx-auto w-full max-w-3xl">
            <Composer
              onSend={sendQuestion}
              onStop={stop}
              streaming={turn.status === "streaming"}
              toolbar={<ScopePicker value={scope} onChange={setScope} disabled={turn.status === "streaming"} />}
            />
          </div>
        </div>
      </section>

      {wide && panel && <aside className="w-[24rem] shrink-0 border-l border-border bg-card">{panel}</aside>}
      {!wide && (
        <Dialog open={Boolean(panel)} onOpenChange={(o) => !o && setSelection(null)}>
          {panel && (
            <SheetContent title="Evidence" bare className="max-w-md">
              {panel}
            </SheetContent>
          )}
        </Dialog>
      )}

      <Dialog open={historyOpen} onOpenChange={setHistoryOpen}>
        <SheetContent title="Conversations" className="right-auto left-0 max-w-xs border-r border-l-0">
          <ConversationSidebar activeId={conversationId} onNavigate={() => setHistoryOpen(false)} />
        </SheetContent>
      </Dialog>
    </div>
  );
}
