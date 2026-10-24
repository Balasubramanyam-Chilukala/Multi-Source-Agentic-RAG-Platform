/** Error, empty and loading states shared by every screen. */
import { AlertTriangle, Check, Copy, RefreshCw } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { isApiError } from "@/api/client";
import { cn } from "@/lib/utils";
import { Button } from "./ui/button";
import { Skeleton } from "./ui/primitives";

export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="inline-flex items-center gap-1 rounded px-1 text-muted-foreground hover:text-foreground"
      aria-label={`${label}: ${value}`}
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? <Check className="size-3" aria-hidden /> : <Copy className="size-3" aria-hidden />}
    </button>
  );
}

/** "Request ID: abc123 [copy]" — users can quote it when reporting a problem. */
export function RequestId({ id }: { id: string | null | undefined }) {
  if (!id) return null;
  return (
    <span className="inline-flex items-center gap-1 font-mono text-[11px] text-muted-foreground">
      Request ID: {id}
      <CopyButton value={id} label="Copy request ID" />
    </span>
  );
}

/** Counts down a Retry-After value. Remount (via `key`) to restart it for a new error. */
export function useCountdown(seconds: number | null | undefined): number {
  const [left, setLeft] = useState(seconds ?? 0);
  useEffect(() => {
    if (!seconds) return;
    const timer = setInterval(() => setLeft((s) => (s <= 1 ? 0 : s - 1)), 1000);
    return () => clearInterval(timer);
  }, [seconds]);
  return left;
}

export function describeError(error: unknown): { message: string; requestId: string | null; retryAfter: number | null; fields: string[] } {
  if (isApiError(error)) {
    return { message: error.message, requestId: error.requestId, retryAfter: error.retryAfter, fields: error.fieldErrors };
  }
  return { message: error instanceof Error ? error.message : "Something went wrong.", requestId: null, retryAfter: null, fields: [] };
}

export function RetryAfterNote({ seconds }: { seconds: number | null }) {
  const left = useCountdown(seconds);
  if (!seconds) return null;
  return <p className="text-xs text-warning">{left > 0 ? `Rate limited — try again in ${left} s.` : "You can try again now."}</p>;
}

/** Inline error box for forms and panels. */
export function InlineError({ error, className }: { error: unknown; className?: string }) {
  if (!error) return null;
  const { message, requestId, retryAfter, fields } = describeError(error);
  return (
    <div role="alert" className={cn("space-y-1 rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger", className)}>
      <p className="font-medium">{message}</p>
      {fields.length > 0 && (
        <ul className="list-disc pl-4 text-xs">
          {fields.map((f) => (
            <li key={f}>{f}</li>
          ))}
        </ul>
      )}
      <RetryAfterNote key={`${requestId}-${retryAfter}`} seconds={retryAfter} />
      <RequestId id={requestId} />
    </div>
  );
}

/** Full-panel error state with optional retry. */
export function ErrorState({ error, onRetry, title = "Couldn't load this" }: { error: unknown; onRetry?: () => void; title?: string }) {
  const { message, requestId, retryAfter } = describeError(error);
  return (
    <div role="alert" className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border px-6 py-10 text-center">
      <div className="rounded-full bg-danger-soft p-2.5 text-danger">
        <AlertTriangle className="size-5" aria-hidden />
      </div>
      <div className="space-y-1">
        <p className="text-sm font-semibold">{title}</p>
        <p className="max-w-md text-sm text-muted-foreground">{message}</p>
      </div>
      <RetryAfterNote key={`${requestId}-${retryAfter}`} seconds={retryAfter} />
      <RequestId id={requestId} />
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw aria-hidden /> Try again
        </Button>
      )}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center gap-3 rounded-lg border border-dashed border-border px-6 py-12 text-center", className)}>
      {icon && <div className="rounded-full bg-accent p-3 text-accent-foreground [&_svg]:size-5">{icon}</div>}
      <div className="space-y-1">
        <p className="text-sm font-semibold">{title}</p>
        {description && <div className="max-w-md text-sm text-muted-foreground">{description}</div>}
      </div>
      {action}
    </div>
  );
}

export function LoadingRows({ rows = 4, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("space-y-2", className)} role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}
