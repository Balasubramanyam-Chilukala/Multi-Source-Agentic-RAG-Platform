import { AlertTriangle, ArrowLeft, MessageSquare } from "lucide-react";
import { Link, useParams } from "react-router";
import { OutcomeBadge, ProviderBadge } from "@/components/badges";
import { KeyValue, PageHeader, Stat } from "@/components/common";
import { ErrorState, LoadingRows } from "@/components/feedback";
import { PageContainer } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/primitives";
import { useRun } from "@/hooks/useRuns";
import { formatDateTime, formatMs, formatNumber, formatScore, humanize, shortId } from "@/lib/utils";
import { NodeTimeline } from "./NodeTimeline";
import { RetrievalAttemptCard } from "./RetrievalAttemptCard";

const CONFIG_LABELS: Record<string, string> = {
  mode: "Retrieval mode",
  top_k: "Top-k",
  candidate_k: "Candidate k",
  rrf_k: "RRF k",
  min_dense_similarity: "Min dense similarity",
  min_lexical_coverage: "Min lexical coverage",
  min_rerank_score: "Min rerank score",
};

function configValue(v: unknown): string {
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : formatScore(v);
  if (v === null || v === undefined) return "—";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

export default function RunDetailPage() {
  const { runId } = useParams();
  const { data: run, isPending, error, refetch } = useRun(runId);

  return (
    <PageContainer>
      <Link to="/runs" className="mb-3 inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3.5" aria-hidden /> All runs
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
                Run <span className="font-mono text-base">{shortId(run.id)}</span> <OutcomeBadge outcome={run.outcome} />
              </span>
            }
            description={`${formatDateTime(run.created_at)}${run.request_id ? ` · request ${run.request_id}` : ""}`}
            actions={
              run.conversation_id ? (
                <Button asChild variant="outline" size="sm">
                  <Link to={`/chat/${run.conversation_id}`}>
                    <MessageSquare aria-hidden /> Open conversation
                  </Link>
                </Button>
              ) : null
            }
          />
          <div className="space-y-4">
            <Card className="grid grid-cols-2 gap-4 p-4 sm:grid-cols-3 lg:grid-cols-6">
              <Stat label="Latency" value={formatMs(run.latency_ms)} />
              <Stat label="Retrieval attempts" value={run.retrieval_attempts} />
              <Stat label="LLM calls" value={run.generation_calls} />
              <Stat label="Prompt tokens" value={formatNumber(run.prompt_tokens)} />
              <Stat label="Completion tokens" value={formatNumber(run.completion_tokens)} />
              <Stat
                label="Cited sentences"
                value={run.details.claims_total ? `${run.details.claims_supported ?? 0}/${run.details.claims_total}` : "—"}
              />
            </Card>

            {(run.error_category || run.details.error) && (
              <Card className="border-danger/40">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-danger">
                    <AlertTriangle className="size-4" aria-hidden /> Error
                  </CardTitle>
                  <CardDescription>Provider details are redacted server-side; only the category and a safe message are stored.</CardDescription>
                </CardHeader>
                <CardContent>
                  <KeyValue
                    items={[
                      ["Category", run.error_category ?? run.details.error?.category ?? "—"],
                      ["Message", run.details.error?.message ?? "—"],
                      ["Failed node", run.details.error?.node ?? "—"],
                    ]}
                  />
                </CardContent>
              </Card>
            )}

            <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
              <Card className="min-w-0">
                <CardHeader>
                  <CardTitle>Node timeline</CardTitle>
                  <CardDescription>Each graph step with its latency, in execution order</CardDescription>
                </CardHeader>
                <CardContent>
                  {run.node_trace.length ? <NodeTimeline trace={run.node_trace} /> : <p className="text-sm text-muted-foreground">No trace recorded.</p>}
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>Configuration</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <ProviderBadge provider={run.provider} model={run.model} />
                  <KeyValue
                    items={[
                      ["Route", humanize(run.details.action ?? undefined)],
                      ["Decided by", humanize(run.details.route_source ?? undefined)],
                      ["Final sufficiency", run.details.final_sufficiency?.sufficient === undefined ? "—" : run.details.final_sufficiency.sufficient ? "Sufficient" : `Insufficient (${humanize(run.details.final_sufficiency.reason)})`],
                      ...Object.entries(run.retrieval_config).map(([k, v]) => [CONFIG_LABELS[k] ?? humanize(k), configValue(v)] as [string, string]),
                    ]}
                  />
                </CardContent>
              </Card>
            </div>

            <Card>
              <CardHeader>
                <CardTitle>Retrieval attempts</CardTitle>
                <CardDescription>
                  Query sent to the retriever, per-chunk scores, and whether the evidence was judged sufficient
                  {run.details.suspicious_chunk_ids?.length ? ` · ${run.details.suspicious_chunk_ids.length} passage(s) flagged as instruction-like` : ""}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {run.details.retrieval_attempts?.length ? (
                  run.details.retrieval_attempts.map((a, i) => (
                    <RetrievalAttemptCard key={i} attempt={a} suspicious={new Set(run.details.suspicious_chunk_ids ?? [])} />
                  ))
                ) : (
                  <p className="text-sm text-muted-foreground">This run did not retrieve (e.g. a calculation, listing, or clarification).</p>
                )}
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </PageContainer>
  );
}
