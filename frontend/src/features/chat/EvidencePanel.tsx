import { AlertTriangle, ExternalLink, FileText, Trash2, X } from "lucide-react";
import { useEffect, useRef } from "react";
import type { Citation, Evidence, Message } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Badge, Skeleton } from "@/components/ui/primitives";
import { useChunk } from "@/hooks/useKnowledge";
import { cn, formatScore, safeExternalUrl } from "@/lib/utils";

export const SUSPICIOUS_WARNING = "Contains instruction-like text — treated as data, not as instructions.";

function SourceLink({ uri }: { uri?: string | null }) {
  const safe = safeExternalUrl(uri);
  if (!safe) return null;
  return (
    <a
      href={safe}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex max-w-full items-center gap-1 text-xs text-primary hover:underline"
    >
      <span className="truncate">{safe}</span>
      <ExternalLink className="size-3 shrink-0" aria-hidden />
    </a>
  );
}

function Location({ section, page }: { section?: string | null; page?: number | null }) {
  if (!section && page == null) return null;
  return (
    <p className="text-xs text-muted-foreground">
      {section && <span>§ {section}</span>}
      {section && page != null && " · "}
      {page != null && <span>Page {page}</span>}
    </p>
  );
}

/** Passage text: inline when available (live answers), otherwise fetched on demand. */
function PassageText({ chunkId, text, clamp }: { chunkId: string; text?: string | null; clamp?: boolean }) {
  const chunk = useChunk(text ? null : chunkId);
  if (text) return <p className={cn("text-sm leading-relaxed whitespace-pre-wrap", clamp && "line-clamp-6")}>{text}</p>;
  if (chunk.isPending) return <Skeleton className="h-16 w-full" />;
  if (chunk.error)
    return (
      <p className="text-xs text-muted-foreground italic">
        {chunk.error && "status" in chunk.error && chunk.error.status === 404 ? "This passage is no longer available (source deleted)." : chunk.error.message}
      </p>
    );
  return <p className={cn("text-sm leading-relaxed whitespace-pre-wrap", clamp && "line-clamp-6")}>{chunk.data.text}</p>;
}

function CitationDetail({ citation }: { citation: Citation }) {
  return (
    <section aria-label={`Citation ${citation.marker}`} className="space-y-2 rounded-lg border border-primary/40 bg-accent/40 p-3">
      <div className="flex items-start gap-2">
        <Badge tone="accent" className="font-mono">
          {citation.marker}
        </Badge>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold">{citation.document_title}</p>
          <Location section={citation.section} page={citation.page_number} />
        </div>
      </div>
      {citation.source_deleted ? (
        <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <Trash2 className="size-3.5" aria-hidden /> Source deleted — this document was removed after the answer was written.
        </p>
      ) : (
        <PassageText chunkId={citation.chunk_id} text={citation.snippet} />
      )}
      <SourceLink uri={citation.source_uri} />
    </section>
  );
}

function EvidenceCard({ item, marker, active }: { item: Evidence; marker?: string; active: boolean }) {
  const ref = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (active) ref.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [active]);
  const scores: Array<[string, number | null | undefined]> = [
    ["dense", item.dense_score],
    ["lexical", item.lexical_score],
    ["fused", item.fused_score],
    ["rerank", item.rerank_score],
  ];
  return (
    <li ref={ref} className={cn("space-y-2 rounded-lg border p-3", active ? "border-primary ring-1 ring-primary/30" : "border-border")}>
      <div className="flex items-start gap-2">
        <span className="mt-0.5 text-xs font-medium text-muted-foreground tabular-nums">#{item.rank ?? "?"}</span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">
            <FileText className="mr-1 inline size-3.5 text-muted-foreground" aria-hidden />
            {item.document_title ?? "Untitled document"}
          </p>
          <Location section={item.section} page={item.page_number} />
        </div>
        {marker ? (
          <Badge tone="accent" className="font-mono" title="Cited in the answer">
            {marker}
          </Badge>
        ) : (
          <Badge tone="neutral" title="Retrieved but not cited">
            not cited
          </Badge>
        )}
      </div>
      {item.suspicious && (
        <p role="note" className="flex items-start gap-1.5 rounded-md bg-warning-soft px-2 py-1.5 text-xs text-warning">
          <AlertTriangle className="mt-px size-3.5 shrink-0" aria-hidden /> {SUSPICIOUS_WARNING}
        </p>
      )}
      <PassageText chunkId={item.chunk_id} text={item.text} clamp />
      <dl className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        {scores.map(([name, value]) => (
          <div key={name} className="flex gap-1">
            <dt>{name}</dt>
            <dd className="font-mono text-foreground">{formatScore(value)}</dd>
          </div>
        ))}
      </dl>
      <SourceLink uri={item.source_uri} />
    </li>
  );
}

export interface EvidencePanelProps {
  message: Message;
  evidence: Evidence[];
  activeMarker: string | null;
  onClose: () => void;
}

export function EvidencePanel({ message, evidence, activeMarker, onClose }: EvidencePanelProps) {
  const markerByChunk = new Map(message.citations.map((c) => [c.chunk_id, c.marker]));
  const active = activeMarker ? message.citations.find((c) => c.marker === activeMarker) : undefined;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold">Evidence</h2>
        <Button variant="ghost" size="icon-sm" aria-label="Close evidence panel" onClick={onClose}>
          <X />
        </Button>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        {active && <CitationDetail citation={active} />}
        <section aria-label="Retrieved passages" className="space-y-2">
          <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
            Retrieved passages ({evidence.length})
          </h3>
          {evidence.length === 0 ? (
            <p className="text-sm text-muted-foreground">No passages were retrieved for this answer.</p>
          ) : (
            <ol className="space-y-2">
              {evidence.map((item) => {
                const marker = markerByChunk.get(item.chunk_id);
                return <EvidenceCard key={item.chunk_id} item={item} marker={marker} active={Boolean(active && active.chunk_id === item.chunk_id)} />;
              })}
            </ol>
          )}
        </section>
      </div>
    </div>
  );
}
