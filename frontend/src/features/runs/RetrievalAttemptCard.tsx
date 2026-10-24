import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import type { RetrievalAttempt } from "@/api/types";
import { Badge, Table, TBody, TD, TH, THead, TR } from "@/components/ui/primitives";
import { formatMs, formatScore, humanize, shortId } from "@/lib/utils";

export function RetrievalAttemptCard({ attempt, suspicious }: { attempt: RetrievalAttempt; suspicious: Set<string> }) {
  const s = attempt.sufficiency;
  return (
    <section className="space-y-3 rounded-lg border border-border p-4" aria-label={`Retrieval attempt ${attempt.attempt ?? ""}`}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">Attempt {attempt.attempt ?? "?"}</h3>
        {attempt.mode && <Badge tone="outline">{humanize(attempt.mode)}</Badge>}
        <Badge tone="neutral">{attempt.results ?? attempt.chunk_ids?.length ?? 0} results</Badge>
        {s && (
          <Badge tone={s.sufficient ? "success" : "warning"}>
            {s.sufficient ? <CheckCircle2 aria-hidden /> : <XCircle aria-hidden />}
            {s.sufficient ? "Sufficient" : "Insufficient"}
          </Badge>
        )}
      </div>
      <p className="rounded-md bg-muted px-3 py-2 font-mono text-xs break-words">{attempt.query ?? "—"}</p>
      {s && (
        <dl className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
          <div>
            <dt className="text-muted-foreground">Reason</dt>
            <dd className="font-medium">{humanize(s.reason)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Top dense sim.</dt>
            <dd className="font-mono">{formatScore(s.top_dense_similarity)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Top lexical cov.</dt>
            <dd className="font-mono">{formatScore(s.top_lexical_coverage)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Top rerank</dt>
            <dd className="font-mono">{formatScore(s.top_rerank_score)}</dd>
          </div>
        </dl>
      )}
      {attempt.scores && attempt.scores.length > 0 && (
        <Table>
          <THead>
            <tr>
              <TH>#</TH>
              <TH>Chunk</TH>
              <TH className="text-right">Dense</TH>
              <TH className="text-right">Lexical</TH>
              <TH className="text-right">Fused</TH>
              <TH className="text-right">Rerank</TH>
            </tr>
          </THead>
          <TBody>
            {attempt.scores.map((score, i) => (
              <TR key={`${score.chunk_id}-${i}`}>
                <TD className="text-xs text-muted-foreground tabular-nums">{i + 1}</TD>
                <TD className="font-mono text-xs">
                  {shortId(score.chunk_id)}
                  {score.chunk_id && suspicious.has(score.chunk_id) && (
                    <span className="ml-1 inline-flex items-center text-warning" title="Contains instruction-like text — treated as data">
                      <AlertTriangle className="size-3" aria-label="suspicious" />
                    </span>
                  )}
                </TD>
                <TD className="text-right font-mono text-xs">{formatScore(score.dense)}</TD>
                <TD className="text-right font-mono text-xs">{formatScore(score.lexical)}</TD>
                <TD className="text-right font-mono text-xs">{formatScore(score.fused)}</TD>
                <TD className="text-right font-mono text-xs">{formatScore(score.rerank)}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        {Object.entries(attempt.timings_ms ?? {}).map(([k, v]) => (
          <span key={k}>
            {humanize(k)}: <span className="font-mono text-foreground">{formatMs(v)}</span>
          </span>
        ))}
        {attempt.embedding_model && <span>Embedding: {attempt.embedding_model}</span>}
        {attempt.reranker && <span>Reranker: {attempt.reranker}</span>}
      </div>
      {attempt.warnings && attempt.warnings.length > 0 && (
        <ul className="space-y-0.5 text-xs text-warning">
          {attempt.warnings.map((w) => (
            <li key={w} className="flex items-center gap-1">
              <AlertTriangle className="size-3" aria-hidden /> {w}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
