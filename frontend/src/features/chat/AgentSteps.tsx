import { Check, Loader2, X } from "lucide-react";
import { cn, formatMs } from "@/lib/utils";
import { currentStepLabel, stepLabel, type AgentStep } from "./streamReducer";

/** Live "what the agent is doing" indicator built from `step` events. */
export function AgentSteps({ steps, live }: { steps: AgentStep[]; live: boolean }) {
  const visible = steps.filter((s) => s.node !== "persist" && s.node !== "finalize" && s.node !== "validate_input");
  return (
    <div className="space-y-2">
      {live && (
        <p className="flex items-center gap-2 text-sm font-medium text-muted-foreground" role="status" aria-live="polite">
          <Loader2 className="size-4 animate-spin text-primary" aria-hidden />
          {currentStepLabel(steps)}
        </p>
      )}
      {visible.length > 0 && (
        <ol className="flex flex-wrap gap-1.5" aria-label="Agent steps">
          {visible.map((s, i) => (
            <li
              key={`${s.node}-${i}`}
              className={cn(
                "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px]",
                s.status === "error" ? "border-danger/40 text-danger" : "border-border text-muted-foreground",
              )}
            >
              {s.status === "started" ? (
                <Loader2 className="size-3 animate-spin" aria-hidden />
              ) : s.status === "error" ? (
                <X className="size-3" aria-hidden />
              ) : (
                <Check className="size-3 text-success" aria-hidden />
              )}
              {stepLabel(s.node)}
              {s.latency_ms != null && <span className="tabular-nums opacity-70">{formatMs(s.latency_ms)}</span>}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
