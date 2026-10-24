import type { NodeTraceEntry } from "@/api/types";
import { cn, formatMs } from "@/lib/utils";
import { stepLabel } from "@/features/chat/streamReducer";

const KNOWN = new Set(["node", "status", "latency_ms", "error_category"]);

function formatExtra(value: unknown): string {
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(3);
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "none";
  if (value === null || value === undefined) return "—";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

/**
 * Waterfall of graph nodes. Nodes run sequentially, so each bar starts where the previous one
 * ended; widths are proportional to latency.
 */
export function NodeTimeline({ trace }: { trace: NodeTraceEntry[] }) {
  const total = trace.reduce((sum, n) => sum + (n.latency_ms ?? 0), 0) || 1;
  // Start offset of each node = sum of the latencies before it.
  const starts = trace.reduce<number[]>((acc, _n, i) => [...acc, i === 0 ? 0 : (acc[i - 1] ?? 0) + (trace[i - 1]?.latency_ms ?? 0)], []);
  return (
    <ol className="space-y-1.5" aria-label="Node timeline">
      {trace.map((entry, i) => {
        const latency = entry.latency_ms ?? 0;
        const left = ((starts[i] ?? 0) / total) * 100;
        const width = Math.max((latency / total) * 100, 0.5);
        const extras = Object.entries(entry).filter(([k]) => !KNOWN.has(k));
        return (
          <li key={`${entry.node}-${i}`} className="grid grid-cols-[8.5rem_1fr_4.5rem] items-center gap-3 text-sm sm:grid-cols-[11rem_1fr_5rem]">
            <div className="min-w-0">
              <p className={cn("truncate font-medium", entry.status === "error" && "text-danger")}>{stepLabel(entry.node)}</p>
              <p className="truncate font-mono text-[11px] text-muted-foreground">
                {entry.node}
                {entry.error_category ? ` · ${entry.error_category}` : ""}
              </p>
            </div>
            <div className="min-w-0 space-y-1">
              <div className="relative h-3 rounded-sm bg-muted" aria-hidden>
                <div
                  className={cn("absolute inset-y-0 rounded-sm", entry.status === "error" ? "bg-danger" : "bg-primary")}
                  style={{ left: `${left}%`, width: `${width}%` }}
                />
              </div>
              {extras.length > 0 && (
                <p className="truncate text-[11px] text-muted-foreground" title={extras.map(([k, v]) => `${k}: ${formatExtra(v)}`).join(" · ")}>
                  {extras.map(([k, v]) => `${k}: ${formatExtra(v)}`).join(" · ")}
                </p>
              )}
            </div>
            <span className="text-right font-mono text-xs tabular-nums">{formatMs(entry.latency_ms)}</span>
          </li>
        );
      })}
    </ol>
  );
}
