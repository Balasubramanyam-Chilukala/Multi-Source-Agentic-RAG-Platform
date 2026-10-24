import { AlertTriangle, Ban, CheckCircle2, CircleDashed, HelpCircle, Loader2, ShieldQuestion, XCircle } from "lucide-react";
import type { DocumentStatus, EvaluationStatus, RunOutcome } from "@/api/types";
import { Badge, type BadgeProps } from "./ui/primitives";

const DOC_STATUS: Record<DocumentStatus, { label: string; tone: BadgeProps["tone"]; icon: typeof Loader2; spin?: boolean }> = {
  pending: { label: "Pending", tone: "neutral", icon: CircleDashed },
  processing: { label: "Processing", tone: "info", icon: Loader2, spin: true },
  ready: { label: "Ready", tone: "success", icon: CheckCircle2 },
  failed: { label: "Failed", tone: "danger", icon: XCircle },
  unsupported: { label: "Unsupported", tone: "warning", icon: Ban },
};

export function DocumentStatusBadge({ status }: { status: DocumentStatus }) {
  const meta = DOC_STATUS[status] ?? { label: status, tone: "neutral" as const, icon: HelpCircle };
  const Icon = meta.icon;
  return (
    <Badge tone={meta.tone} data-status={status}>
      <Icon className={meta.spin ? "animate-spin" : undefined} aria-hidden />
      {meta.label}
    </Badge>
  );
}

const OUTCOME: Record<RunOutcome, { label: string; tone: BadgeProps["tone"]; icon: typeof Loader2 }> = {
  answered: { label: "Answered", tone: "success", icon: CheckCircle2 },
  abstained: { label: "Not enough evidence", tone: "warning", icon: ShieldQuestion },
  clarification: { label: "Needs clarification", tone: "info", icon: HelpCircle },
  error: { label: "Error", tone: "danger", icon: AlertTriangle },
};

export function OutcomeBadge({ outcome }: { outcome: RunOutcome | null | undefined }) {
  if (!outcome) return <Badge tone="neutral">Unknown</Badge>;
  const meta = OUTCOME[outcome] ?? { label: outcome, tone: "neutral" as const, icon: HelpCircle };
  const Icon = meta.icon;
  return (
    <Badge tone={meta.tone} data-outcome={outcome}>
      <Icon aria-hidden />
      {meta.label}
    </Badge>
  );
}

const EVAL_STATUS: Record<EvaluationStatus, { label: string; tone: BadgeProps["tone"]; spin?: boolean }> = {
  queued: { label: "Queued", tone: "neutral" },
  running: { label: "Running", tone: "info", spin: true },
  succeeded: { label: "Succeeded", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
};

export function EvaluationStatusBadge({ status }: { status: EvaluationStatus }) {
  const meta = EVAL_STATUS[status] ?? { label: status, tone: "neutral" as const };
  return (
    <Badge tone={meta.tone}>
      {meta.spin && <Loader2 className="animate-spin" aria-hidden />}
      {meta.label}
    </Badge>
  );
}

/** Provider/model chip with a "demo" marker for the deterministic mock provider. */
export function ProviderBadge({ provider, model }: { provider?: string | null; model?: string | null }) {
  if (!provider && !model) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <Badge tone="outline" title="Provider / model that produced this answer">
        {provider ?? "?"}
        {model ? <span className="text-muted-foreground">· {model}</span> : null}
      </Badge>
      {provider === "mock" && (
        <Badge tone="warning" title="Deterministic extractive mock provider — no LLM was called">
          demo / mock
        </Badge>
      )}
    </span>
  );
}
