import { FlaskConical, Play } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { isApiError } from "@/api/client";
import { RETRIEVAL_MODES, type EvaluationRun } from "@/api/types";
import { EvaluationStatusBadge, ProviderBadge } from "@/components/badges";
import { PageHeader, RoleGate } from "@/components/common";
import { EmptyState, ErrorState, LoadingRows } from "@/components/feedback";
import { PageContainer } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/button";
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Input, Label, Select, Table, TBody, TD, TH, THead, TR } from "@/components/ui/primitives";
import { useEvaluationDatasets, useEvaluations } from "@/hooks/useEvaluations";
import { formatDateTime, formatMs, formatPercent, formatScore, humanize, shortId } from "@/lib/utils";
import { ANSWER_METRIC_KEYS, RETRIEVAL_METRIC_KEYS, latestByMode, metricLabel } from "./metrics";
import { RetrievalChart } from "./RetrievalChart";
import { RunEvaluationDialog } from "./RunEvaluationDialog";

function ComparisonTable({ runs }: { runs: EvaluationRun[] }) {
  const k = runs[0]?.config.top_k;
  const hasAnswers = runs.some((r) => r.metrics?.answer);
  return (
    <Table>
      <THead>
        <tr>
          <TH>Mode</TH>
          {RETRIEVAL_METRIC_KEYS.map((m) => (
            <TH key={m} className="text-right">
              {metricLabel(m, k)}
            </TH>
          ))}
          <TH className="text-right">Retrieval p50 / p95</TH>
          <TH className="text-right">Error rate</TH>
        </tr>
      </THead>
      <TBody>
        {runs.map((r) => (
          <TR key={r.id}>
            <TD className="font-medium whitespace-nowrap">
              <Link to={`/evaluations/${r.id}`} className="hover:underline">
                {humanize(r.retrieval_mode)}
              </Link>
            </TD>
            {RETRIEVAL_METRIC_KEYS.map((m) => (
              <TD key={m} className="text-right font-mono text-xs">
                {formatScore(r.metrics?.retrieval?.[m])}
              </TD>
            ))}
            <TD className="text-right font-mono text-xs whitespace-nowrap">
              {formatMs(r.metrics?.latency_ms?.retrieval_p50)} / {formatMs(r.metrics?.latency_ms?.retrieval_p95)}
            </TD>
            <TD className="text-right font-mono text-xs">{formatPercent(r.metrics?.error_rate)}</TD>
          </TR>
        ))}
      </TBody>
      {hasAnswers && <caption className="pt-2 text-left text-xs text-muted-foreground">Answer metrics are shown in the card below.</caption>}
    </Table>
  );
}

function AnswerMetricsCard({ runs }: { runs: EvaluationRun[] }) {
  const withAnswers = runs.filter((r) => r.metrics?.answer);
  if (withAnswers.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Answer quality</CardTitle>
        <CardDescription>
          Deterministic, reference-based scores (string/marker matching against the dataset's references) — not an LLM judge.
        </CardDescription>
      </CardHeader>
      <CardContent className="px-0 pb-0">
        <Table>
          <THead>
            <tr>
              <TH>Mode</TH>
              {ANSWER_METRIC_KEYS.map((m) => (
                <TH key={m} className="text-right">
                  {metricLabel(m)}
                </TH>
              ))}
              <TH className="text-right">End-to-end p50 / p95</TH>
              <TH>Model</TH>
            </tr>
          </THead>
          <TBody>
            {withAnswers.map((r) => (
              <TR key={r.id}>
                <TD className="font-medium">{humanize(r.retrieval_mode)}</TD>
                {ANSWER_METRIC_KEYS.map((m) => (
                  <TD key={m} className="text-right font-mono text-xs">
                    {formatScore(r.metrics?.answer?.[m])}
                  </TD>
                ))}
                <TD className="text-right font-mono text-xs whitespace-nowrap">
                  {formatMs(r.metrics?.latency_ms?.end_to_end_p50)} / {formatMs(r.metrics?.latency_ms?.end_to_end_p95)}
                </TD>
                <TD>
                  <ProviderBadge provider={r.config.provider} model={r.config.model} />
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function HistoryTable({ dataset }: { dataset?: string }) {
  const [params, setParams] = useSearchParams();
  const mode = params.get("mode") ?? "";
  const status = params.get("status") ?? "";
  const since = params.get("since") ?? "";
  const filters = { dataset: params.get("dataset") ?? dataset, mode: mode || undefined, status: status || undefined, limit: 50 };
  const { data, isPending, error, refetch } = useEvaluations(filters);
  const set = (key: string, value: string) =>
    setParams((p) => {
      if (value) p.set(key, value);
      else p.delete(key);
      return p;
    });
  // The API has no date filter; "since" is applied client-side to the loaded page.
  const items = useMemo(
    () => (data?.items ?? []).filter((r) => !since || r.created_at.slice(0, 10) >= since),
    [data, since],
  );

  return (
    <Card>
      <CardHeader className="gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <CardTitle>Run history</CardTitle>
          <CardDescription>Polls automatically while runs are queued or running.</CardDescription>
        </div>
        <div className="flex flex-wrap gap-2">
          <div className="space-y-1">
            <Label htmlFor="f-mode" className="text-xs text-muted-foreground">
              Mode
            </Label>
            <Select id="f-mode" className="h-8 w-36 text-xs" value={mode} onChange={(e) => set("mode", e.target.value)}>
              <option value="">All modes</option>
              {RETRIEVAL_MODES.map((m) => (
                <option key={m} value={m}>
                  {humanize(m)}
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="f-status" className="text-xs text-muted-foreground">
              Status
            </Label>
            <Select id="f-status" className="h-8 w-32 text-xs" value={status} onChange={(e) => set("status", e.target.value)}>
              <option value="">All</option>
              {["queued", "running", "succeeded", "failed"].map((s) => (
                <option key={s} value={s}>
                  {humanize(s)}
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="f-since" className="text-xs text-muted-foreground">
              Since
            </Label>
            <Input id="f-since" type="date" className="h-8 w-38 text-xs" value={since} onChange={(e) => set("since", e.target.value)} />
          </div>
        </div>
      </CardHeader>
      <CardContent className="px-0 pb-0">
        {isPending ? (
          <LoadingRows className="px-5 pb-5" />
        ) : error ? (
          <div className="px-5 pb-5">
            <ErrorState error={error} onRetry={() => void refetch()} />
          </div>
        ) : items.length === 0 ? (
          <p className="px-5 pb-5 text-sm text-muted-foreground">No runs match these filters.</p>
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Run</TH>
                <TH>Dataset</TH>
                <TH>Mode</TH>
                <TH>Status</TH>
                <TH className="hidden text-right md:table-cell">Recall@k</TH>
                <TH className="hidden text-right md:table-cell">MRR</TH>
                <TH className="hidden lg:table-cell">Created</TH>
              </tr>
            </THead>
            <TBody>
              {items.map((r) => (
                <TR key={r.id}>
                  <TD>
                    <Link to={`/evaluations/${r.id}`} className="font-mono text-xs font-medium text-primary hover:underline">
                      {shortId(r.id)}
                    </Link>
                    {r.config.include_answers && (
                      <Badge tone="accent" className="ml-1.5">
                        answers
                      </Badge>
                    )}
                  </TD>
                  <TD className="text-xs whitespace-nowrap">
                    {r.dataset_name}
                    {r.dataset_version && <span className="text-muted-foreground"> v{r.dataset_version}</span>}
                  </TD>
                  <TD className="text-xs whitespace-nowrap">{humanize(r.retrieval_mode)}</TD>
                  <TD>
                    <EvaluationStatusBadge status={r.status} />
                    {r.error_message && <p className="mt-0.5 max-w-48 truncate text-[11px] text-danger" title={r.error_message}>{r.error_message}</p>}
                  </TD>
                  <TD className="hidden text-right font-mono text-xs md:table-cell">{formatScore(r.metrics?.retrieval?.["recall@k"])}</TD>
                  <TD className="hidden text-right font-mono text-xs md:table-cell">{formatScore(r.metrics?.retrieval?.mrr)}</TD>
                  <TD className="hidden text-xs whitespace-nowrap text-muted-foreground lg:table-cell">{formatDateTime(r.created_at)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

export default function EvaluationsPage() {
  const [params, setParams] = useSearchParams();
  const datasets = useEvaluationDatasets();
  const [dialogOpen, setDialogOpen] = useState(false);
  const datasetName = params.get("dataset") ?? datasets.data?.[0]?.name;
  const comparison = useEvaluations({ dataset: datasetName, limit: 100 });
  const latest = useMemo(() => latestByMode(comparison.data?.items ?? [], datasetName), [comparison.data, datasetName]);
  const dataset = datasets.data?.find((d) => d.name === datasetName);
  const notDeployed = isApiError(datasets.error) && datasets.error.status === 404;

  const runButton = (
    <RoleGate permission="runEvaluations" fallback="disabled" reason="Only admins and owners can run evaluations">
      <Button size="sm" onClick={() => setDialogOpen(true)} disabled={notDeployed}>
        <Play aria-hidden /> Run evaluation
      </Button>
    </RoleGate>
  );

  return (
    <PageContainer>
      <PageHeader
        title="Evaluation"
        description="Reference-based retrieval and answer quality, compared across retrieval modes."
        actions={
          <>
            {datasets.data && datasets.data.length > 1 && (
              <Select
                aria-label="Dataset"
                className="h-8 w-52 text-xs"
                value={datasetName}
                onChange={(e) =>
                  setParams((p) => {
                    p.set("dataset", e.target.value);
                    return p;
                  })
                }
              >
                {datasets.data.map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.name}
                  </option>
                ))}
              </Select>
            )}
            {runButton}
          </>
        }
      />

      {notDeployed ? (
        <EmptyState
          icon={<FlaskConical />}
          title="Evaluation API not available"
          description="This server doesn't expose /api/v1/evaluations yet. Once the backend ships it, results will appear here."
        />
      ) : datasets.error ? (
        <ErrorState error={datasets.error} onRetry={() => void datasets.refetch()} />
      ) : (
        <div className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
            <Card>
              <CardHeader>
                <CardTitle>Dataset</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {datasets.isPending ? (
                  <LoadingRows rows={3} />
                ) : !dataset ? (
                  <p className="text-sm text-muted-foreground">No datasets found on the server.</p>
                ) : (
                  <>
                    <div>
                      <p className="font-mono text-sm font-medium">{dataset.name}</p>
                      <p className="text-xs text-muted-foreground">Version {dataset.version}</p>
                    </div>
                    {dataset.description && <p className="text-xs text-muted-foreground">{dataset.description}</p>}
                    <dl className="grid grid-cols-2 gap-2 text-sm">
                      <div>
                        <dt className="text-xs text-muted-foreground">Cases</dt>
                        <dd className="text-xl font-semibold tabular-nums">{dataset.case_count}</dd>
                      </div>
                      <div>
                        <dt className="text-xs text-muted-foreground">Documents</dt>
                        <dd className="text-xl font-semibold tabular-nums">{dataset.document_count}</dd>
                      </div>
                    </dl>
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(dataset.categories).map(([cat, n]) => (
                        <Badge key={cat} tone="neutral">
                          {humanize(cat)} · {n}
                        </Badge>
                      ))}
                    </div>
                  </>
                )}
              </CardContent>
            </Card>
            <Card className="min-w-0">
              <CardHeader>
                <CardTitle>Retrieval metrics by mode</CardTitle>
                <CardDescription>Latest successful run per mode{latest[0]?.config.top_k ? ` · k = ${latest[0].config.top_k}` : ""}</CardDescription>
              </CardHeader>
              <CardContent>
                {comparison.isPending ? (
                  <LoadingRows rows={4} />
                ) : comparison.error ? (
                  <ErrorState error={comparison.error} onRetry={() => void comparison.refetch()} />
                ) : latest.length === 0 ? (
                  <EmptyState
                    icon={<FlaskConical />}
                    title="No completed evaluation runs yet"
                    description="Run an evaluation to compare dense, lexical, hybrid and reranked retrieval on the labelled dataset."
                    action={runButton}
                  />
                ) : (
                  <RetrievalChart runs={latest} />
                )}
              </CardContent>
            </Card>
          </div>
          {latest.length > 0 && (
            <Card>
              <CardContent className="px-0 pt-2 pb-0">
                <ComparisonTable runs={latest} />
              </CardContent>
            </Card>
          )}
          <AnswerMetricsCard runs={latest} />
          <HistoryTable dataset={datasetName} />
        </div>
      )}

      <RunEvaluationDialog open={dialogOpen} onOpenChange={setDialogOpen} datasets={datasets.data ?? []} defaultDataset={datasetName} />
    </PageContainer>
  );
}
