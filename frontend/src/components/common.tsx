import { useState, type ReactNode } from "react";
import { useAuth } from "@/lib/auth";
import type { Permission } from "@/lib/roles";
import { InlineError } from "./feedback";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogFooter } from "./ui/overlays";
import { Tooltip } from "./ui/overlays";

export function PageHeader({ title, description, actions }: { title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex flex-col gap-3 pb-5 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0 space-y-1">
        <h1 className="truncate text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/**
 * Renders children only when the current role has the permission. With `fallback="disabled"`
 * the action stays visible but disabled, with a tooltip explaining why.
 */
export function RoleGate({
  permission,
  children,
  fallback = "hide",
  reason,
}: {
  permission: Permission;
  children: ReactNode;
  fallback?: "hide" | "disabled";
  reason?: string;
}) {
  const { can, role } = useAuth();
  if (can(permission)) return <>{children}</>;
  if (fallback === "hide") return null;
  return (
    <Tooltip content={reason ?? `Your role (${role ?? "unknown"}) can't do this`}>
      {/* Focusable so keyboard users can reach the tooltip that explains why the action is unavailable. */}
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
      <span tabIndex={0} className="inline-flex cursor-not-allowed" data-testid={`gated-${permission}`}>
        <span className="pointer-events-none opacity-50" aria-disabled="true" inert>
          {children}
        </span>
      </span>
    </Tooltip>
  );
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Delete",
  onConfirm,
  destructive = true,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: ReactNode;
  confirmLabel?: string;
  onConfirm: () => Promise<unknown>;
  destructive?: boolean;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setError(null);
        onOpenChange(next);
      }}
    >
      <DialogContent title={title} description={description} role="alertdialog">
        <InlineError error={error} />
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={pending}>
            Cancel
          </Button>
          <Button
            variant={destructive ? "destructive" : "default"}
            loading={pending}
            onClick={async () => {
              setPending(true);
              setError(null);
              try {
                await onConfirm();
                onOpenChange(false);
              } catch (e) {
                setError(e);
              } finally {
                setPending(false);
              }
            }}
          >
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="min-w-0 space-y-1">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p className="text-2xl font-semibold tabular-nums tracking-tight">{value}</p>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function KeyValue({ items }: { items: Array<[string, ReactNode]> }) {
  return (
    <dl className="grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-4 gap-y-2 text-sm">
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="min-w-0 break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
