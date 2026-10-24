import { AlertTriangle, Loader2, RotateCcw, WifiOff } from "lucide-react";
import { RequestId, RetryAfterNote } from "@/components/feedback";
import { Button } from "@/components/ui/button";
import { AgentSteps } from "./AgentSteps";
import { AnswerMarkdown } from "./AnswerMarkdown";
import type { TurnState } from "./streamReducer";
import { UserBubble } from "./Thread";

/** The in-flight turn: the question, live agent steps, and the provisional streamed draft. */
export function PendingTurn({ turn, recovering, onRetry }: { turn: TurnState; recovering: boolean; onRetry: () => void }) {
  return (
    <div className="space-y-5">
      <UserBubble content={turn.question} />
      <div className="space-y-3 rounded-lg border border-border bg-card p-4" aria-live="polite" aria-busy={turn.status === "streaming"}>
        {turn.status === "streaming" && <AgentSteps steps={turn.steps} live />}
        {turn.draft && (
          <div className="opacity-90">
            {/* Provisional text: citations are validated when the final answer arrives. */}
            <AnswerMarkdown content={turn.draft} citations={[]} />
            {turn.status === "streaming" && <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-primary align-middle" aria-hidden />}
          </div>
        )}
        {turn.resets > 0 && turn.status === "streaming" && (
          <p className="text-xs text-muted-foreground">Regenerating the answer to fix its citations…</p>
        )}
        {turn.status === "error" && turn.error && (
          <div role="alert" className="space-y-2 rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
            <p className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
              <span className="min-w-0 flex-1">{turn.error.message}</span>
            </p>
            <RetryAfterNote key={`${turn.error.requestId}-${turn.error.retryAfter}`} seconds={turn.error.retryAfter ?? null} />
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" variant="outline" onClick={onRetry}>
                <RotateCcw aria-hidden /> Retry
              </Button>
              <RequestId id={turn.error.requestId} />
            </div>
          </div>
        )}
        {turn.status === "interrupted" && (
          <div className="flex items-start gap-2 rounded-md bg-muted px-3 py-2 text-sm text-muted-foreground" role="status">
            {turn.interruption === "network" ? <WifiOff className="mt-0.5 size-4 shrink-0" aria-hidden /> : null}
            <div className="min-w-0 flex-1 space-y-1">
              <p>
                {turn.interruption === "aborted" ? "Stopped." : "The connection was interrupted."}{" "}
                {turn.conversationId
                  ? "The server keeps working and saves the answer to this conversation."
                  : "The request may not have reached the server."}
              </p>
              {recovering && (
                <p className="flex items-center gap-1.5 text-xs">
                  <Loader2 className="size-3 animate-spin" aria-hidden /> Checking for the saved answer…
                </p>
              )}
            </div>
            {!recovering && (
              <Button size="sm" variant="outline" onClick={onRetry}>
                <RotateCcw aria-hidden /> Ask again
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
