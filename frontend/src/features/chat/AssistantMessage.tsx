import { Activity, AlertTriangle, HelpCircle, Layers, RotateCcw, ShieldQuestion, ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import type { Message } from "@/api/types";
import { OutcomeBadge, ProviderBadge } from "@/components/badges";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/controls";
import { Tooltip } from "@/components/ui/overlays";
import { useSubmitFeedback } from "@/hooks/useConversations";
import { cn } from "@/lib/utils";
import { AnswerMarkdown } from "./AnswerMarkdown";

export interface AssistantMessageProps {
  message: Message;
  conversationId: string | undefined;
  evidenceCount: number;
  selected: boolean;
  activeMarker: string | null;
  onOpenEvidence: (marker: string | null) => void;
  onRetry?: () => void;
}

function OutcomeBanner({ message, onRetry }: { message: Message; onRetry?: () => void }) {
  const outcome = message.metadata.outcome;
  if (outcome === "abstained") {
    return (
      <div className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning-soft px-3 py-2 text-sm text-warning">
        <ShieldQuestion className="mt-0.5 size-4 shrink-0" aria-hidden />
        <p>
          <span className="font-semibold">Not enough evidence.</span> Atlas couldn't find support for an answer in your sources, so it
          didn't guess. Try rephrasing, widening the collection scope, or adding documents.
        </p>
      </div>
    );
  }
  if (outcome === "clarification") {
    return (
      <div className="flex items-center gap-2 text-xs font-medium text-info">
        <HelpCircle className="size-3.5" aria-hidden /> Atlas needs a bit more detail
      </div>
    );
  }
  if (outcome === "error") {
    return (
      <div role="alert" className="flex flex-wrap items-start gap-2 rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
        <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
        <p className="min-w-0 flex-1">{message.metadata.error?.message ?? (message.content || "The answer could not be completed.")}</p>
        {onRetry && (
          <Button size="sm" variant="outline" onClick={onRetry}>
            <RotateCcw aria-hidden /> Retry
          </Button>
        )}
      </div>
    );
  }
  return null;
}

export function AssistantMessage({ message, conversationId, evidenceCount, selected, activeMarker, onOpenEvidence, onRetry }: AssistantMessageProps) {
  const [highlight, setHighlight] = useState(false);
  const feedback = useSubmitFeedback(conversationId);
  const [localRating, setLocalRating] = useState<number | null>(null);
  const rating = localRating ?? message.feedback;
  const outcome = message.metadata.outcome;
  const claims = message.metadata.claims ?? [];
  const unsupported = claims.filter((c) => !c.supported);
  const showBody = outcome !== "error" && message.content;

  function rate(value: 1 | -1) {
    setLocalRating(value);
    feedback.mutate(
      { message_id: message.id, rating: value },
      {
        onSuccess: () => toast.success("Thanks for the feedback"),
        onError: (e) => {
          setLocalRating(null);
          toast.error(e.message);
        },
      },
    );
  }

  return (
    <article
      aria-label="Assistant answer"
      className={cn("space-y-3 rounded-lg border bg-card p-4 transition-colors", selected ? "border-primary/50" : "border-border")}
      data-outcome={outcome}
    >
      <OutcomeBanner message={message} onRetry={onRetry} />
      {showBody && (
        <AnswerMarkdown
          content={message.content}
          citations={message.citations}
          activeMarker={selected ? activeMarker : null}
          onCitationClick={(marker) => onOpenEvidence(marker)}
          highlightUnsupported={highlight ? unsupported.map((c) => c.text) : undefined}
        />
      )}
      {highlight && unsupported.length > 0 && (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <mark className="unsupported rounded-sm bg-warning-soft px-1 text-foreground">highlighted</mark> sentences are general context (not from
          sources).
        </p>
      )}
      <footer className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-border pt-3 text-xs">
        {outcome && outcome !== "answered" && <OutcomeBadge outcome={outcome} />}
        <ProviderBadge provider={message.metadata.provider} model={message.metadata.model} />
        {claims.length > 0 && outcome === "answered" && (
          <span className="inline-flex items-center gap-2 text-muted-foreground">
            <span className="tabular-nums">
              {claims.length - unsupported.length}/{claims.length} sentences cited
            </span>
            {unsupported.length > 0 && (
              <label className="inline-flex cursor-pointer items-center gap-1.5">
                <Switch checked={highlight} onCheckedChange={setHighlight} aria-label="Highlight general context (sentences not from sources)" />
                Highlight uncited
              </label>
            )}
          </span>
        )}
        <span className="ml-auto inline-flex items-center gap-1">
          {evidenceCount > 0 && (
            <Button variant="ghost" size="sm" onClick={() => onOpenEvidence(null)} aria-label={`Show evidence (${evidenceCount} passages)`}>
              <Layers aria-hidden /> Evidence ({evidenceCount})
            </Button>
          )}
          {message.run_id && (
            <Button asChild variant="ghost" size="sm">
              <Link to={`/runs/${message.run_id}`} aria-label="Open run trace">
                <Activity aria-hidden /> Trace
              </Link>
            </Button>
          )}
          <Tooltip content="Helpful">
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Thumbs up"
              aria-pressed={rating === 1}
              className={cn(rating === 1 && "bg-success-soft text-success")}
              onClick={() => rate(1)}
              disabled={feedback.isPending}
            >
              <ThumbsUp />
            </Button>
          </Tooltip>
          <Tooltip content="Not helpful">
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Thumbs down"
              aria-pressed={rating === -1}
              className={cn(rating === -1 && "bg-danger-soft text-danger")}
              onClick={() => rate(-1)}
              disabled={feedback.isPending}
            >
              <ThumbsDown />
            </Button>
          </Tooltip>
        </span>
      </footer>
    </article>
  );
}
