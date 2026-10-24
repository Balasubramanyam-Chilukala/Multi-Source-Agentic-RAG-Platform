import { AlertTriangle, ArrowLeft } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router";
import type { EvaluationRunDetail } from "@/api/types";
import { EvaluationStatusBadge, ProviderBadge } from "@/components/badges";
import { KeyValue, PageHeader, Stat } from "@/components/common";
import { ErrorState, LoadingRows } from "@/components/feedback";
import { PageContainer } from "@/components/layout/AppShell";
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Label, Select, Table, TBody, TD, TH, THead, TR } from "@/components/ui/primitives";
import { useEvaluation } from "@/hooks/useEvaluations";
import { isEvaluationActive } from "@/hooks/polling";
import { formatDateTime, formatMs, formatPercent, formatScore, humanize, shortId } from "@/lib/utils";
import { ANSWER_METRIC_KEYS, RETRIEVAL_METRIC_KEYS, metricLabel } from "./metrics";

function ScoreBar({ value }: { value: number | null | undefined }) {
  const pct = typeof value === "number" ? Math.max(0, Math.min(1, value)) * 100 : 0;
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 rounded-full bg-muted" aria-hidden>
        <div className="h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
      </div>
      <span className="font-mono text-xs tabular-nums">{formatScore(value)}</span>
    </div>
  );
}

function CategoryBreakdown({ run }: { run: EvaluationRunDetail }) {
  const entries = Object.entries(run.metrics?.by_category ?? {});
  if (entries.length === 0) return <p className="text-sm text-muted-foreground">No per-category breakdown.</p>;
  const k = run.config.top_k;
  return (
    <Table>
      <THead>
        <tr>
          <TH>Category</TH>
          <TH className="text-right">Cases</TH>
          <TH>{metricLabel("recall@k", k)}</TH>
          <TH>MRR</TH>
        </tr>
      </THead>
      <TBody>
        {entries.map(([cat, m]) => (
          <TR key={cat}>
            <TD className="font-medium">{humanize(cat)}</TD>
            <TD className="text-right tabular-nums">{m.count ?? "—"}</TD>
            <TD>
              <ScoreBar value={m["recall@k"]} />
            </TD>
            <TD>
              <ScoreBar value={m.mrr} />
            </TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}

function CasesTable({ run }: { run: EvaluationRunDetail }) {
  const [category, setCategory] = useState("");
  const categories = useMemo(() => [...new Set((run.cases ?? []).map((c) => c.category ?? "uncategorized"))].sort(), [run.cases]);
  const cases = (run.cases ?? []).filter((c) => !category || (c.category ?? "uncategorized") === category);
  const hasAnswers = (run.cases ?? []).some((c) => c.answer_metrics && Object.keys(c.answer_metrics).length);
  const k = run.config.top_k;
  return (
    <Card>
      <CardHeader className="gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <CardTitle>Cases ({(run.cases ?? []).length})</CardTitle>
          <CardDescription>Per-question retrieval{hasAnswers ? " and answer" : ""} scores</CardDescription>
        </div>
        <div className="space-y-1">
          <Label htmlFor="case-cat" className="text-xs text-muted-foreground">
            Category
          </Label>
          <Select id="case-cat" className="h-8 w-44 text-xs" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All categories</option>
            {categories.map((c) => (
              <option key={c} value={c}>
                {humanize(c)}
              </option>
            ))}
          </Select>
        </div>
      </CardHeader>
      <CardContent className="px-0 pb-0">
        <Table>
          <THead>
            <tr>
              <TH>Question</TH>
              <TH>Category</TH>
              <TH className="text-right">{metricLabel("recall@k", k)}</TH>
              <TH className="text-right">MRR</TH>
              <TH className="hidden text-right md:table-cell">{metricLabel("ndcg@k", k)}</TH>
              {hasAnswers && <TH className="hidden text-right md:table-cell">Correct</TH>}
              <TH className="hidden text-right sm:table-cell">Latency</TH>
            </tr>
          </THead>
          <TBody>
            {cases.map((c) => (
              <TR key={c.case_id}>
                <TD className="max-w-md min-w-56">
                  <details>
                    <summary className="cursor-pointer text-sm">
                      <span className="mr-1.5 font-mono text-[11px] text-muted-foreground">{c.case_id}</span>
                      {c.question}
                    </summary>
                    <div className="mt-2 space-y-2 text-xs">
                      {c.error_message && (
                        <p className="flex items-center gap-1 text-danger">
                          <AlertTriangle className="size-3" aria-hidden /> {c.error_message}
                        </p>
                      )}
                      {c.answer && <p className="rounded bg-muted p-2 whitespace-pre-wrap">{c.answer}</p>}
                      {c.answer_metrics && (
                        <p className="text-muted-foreground">
                          {ANSWER_METRIC_KEYS.filter((m) => c.answer_metrics?.[m] != null)
                            .map((m) => `${metricLabel(m)} ${formatScore(c.answer_metrics?.[m])}`)
                            .join(" · ")}
                        </p>
                      )}
                      <p className="font-mono break-all text-muted-foreground">
                        Retrieved: {c.retrieved_chunk_ids.length ? c.retrieved_chunk_ids.map(shortId).join(", ") : "none"}
                      </p>
                    </div>
                  </details>
                </TD>
                <TD>
                  <Badge tone="neutral">{humanize(c.category ?? "uncategorized")}</Badge>
                </TD>
                <TD className="text-right font-mono text-xs">{formatScore(c.retrieval_metrics?.["recall@k"])}</TD>
                <TD className="text-right font-mono text-xs">{formatScore(c.retrieval_metrics?.mrr)}</TD>
                <TD className="hidden text-right font-mono text-xs md:table-cell">{formatScore(c.retrieval_metrics?.["ndcg@k"])}</TD>
                {hasAnswers && <TD className="hidden text-right font-mono text-xs md:table-cell">{formatScore(c.answer_metrics?.correctness)}</TD>}
                <TD className="hidden text-right font-mono text-xs sm:table-cell">{formatMs(c.latency_ms)}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}

export default function EvaluationRunPage() {
  const { runId } = useParams();
  const { data: run, isPending, error, refetch } = useEvaluation(runId);
  const k = run?.config.top_k;

  return (
    <PageContainer>
      <Link to="/evaluations" className="mb-3 inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3.5" aria-hidden /> Evaluation
      </Link>
      {isPending ? (
        <LoadingRows rows={6} />
      ) : error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : (
        <>
          <PageHeader
            title={
              <span className="flex flex-wrap items-center gap-2">
                {humanize(run.retrieval_mode)} · <span className="font-mono text-base">{run.dataset_name}</span>
                <EvaluationStatusBadge status={run.status} />
              </span>
            }
            description={`Run ${shortId(run.id)} · created ${formatDateTime(run.created_at)}`}
          />
          {isEvaluationActive(run.status) && (
            <p className="mb-4 rounded-md bg-info-soft px-3 py-2 text-sm text-info" role="status">
              This run is {run.status}. The page refreshes automatically.
            </p>
          )}
          {run.error_message && (
            <p className="mb-4 flex items-start gap-2 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger" role="alert">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden /> {run.error_message}
            </p>
          )}
          <div className="space-y-4">
            <Card className="grid grid-cols-2 gap-4 p-4 sm:grid-cols-3 lg:grid-cols-6">
              <Stat
                label="Cases"
                value={run.metrics?.case_count ?? (run.cases ?? []).length}
                hint={run.metrics?.labelled_case_count != null ? `${run.metrics.labelled_case_count} with relevance labels` : undefined}
              />
              {RETRIEVAL_METRIC_KEYS.map((m) => (
                <Stat key={m} label={metricLabel(m, k)} value={formatScore(run.metrics?.retrieval?.[m])} />
              ))}
            </Card>
            <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
              <Card className="min-w-0">
                <CardHeader>
                  <CardTitle>By category</CardTitle>
                </CardHeader>
                <CardContent className="px-0 pb-0">
                  <CategoryBreakdown run={run} />
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>Run configuration</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <ProviderBadge provider={run.config.provider} model={run.config.model} />
                  <KeyValue
                    items={[
                      ["Top-k", run.config.top_k ?? "—"],
                      ["Embedding", run.config.embedding_model ?? "—"],
                      ["Reranker", run.config.reranker ?? "—"],
                      ["Answers", run.config.include_answers ? "Included" : "Retrieval only"],
                      ["Retrieval p50 / p95", `${formatMs(run.metrics?.latency_ms?.retrieval_p50)} / ${formatMs(run.metrics?.latency_ms?.retrieval_p95)}`],
                      ...(run.config.include_answers
                        ? [["End-to-end p50 / p95", `${formatMs(run.metrics?.latency_ms?.end_to_end_p50)} / ${formatMs(run.metrics?.latency_ms?.end_to_end_p95)}`] as [string, string]]
                        : []),
                      ["Error rate", formatPercent(run.metrics?.error_rate)],
                      ["Started", formatDateTime(run.started_at)],
                      ["Finished", formatDateTime(run.finished_at)],
                    ]}
                  />
                </CardContent>
              </Card>
            </div>
            {run.metrics?.answer && (
              <Card>
                <CardHeader>
                  <CardTitle>Answer quality</CardTitle>
                  <CardDescription>Deterministic, reference-based scores — not an LLM judge.</CardDescription>
                </CardHeader>
                <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-5">
                  {ANSWER_METRIC_KEYS.map((m) => (
                    <Stat key={m} label={metricLabel(m)} value={formatScore(run.metrics?.answer?.[m])} />
                  ))}
                </CardContent>
              </Card>
            )}
            <CasesTable run={run} />
          </div>
        </>
      )}
    </PageContainer>
  );
}
