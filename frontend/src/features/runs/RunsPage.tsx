import { Activity, ChevronLeft, ChevronRight } from "lucide-react";
import { Link, useSearchParams } from "react-router";
import type { RunOutcome } from "@/api/types";
import { OutcomeBadge, ProviderBadge } from "@/components/badges";
import { PageHeader } from "@/components/common";
import { EmptyState, ErrorState, LoadingRows } from "@/components/feedback";
import { PageContainer } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/button";
import { Card, Label, Select, Table, TBody, TD, TH, THead, TR } from "@/components/ui/primitives";
import { useRuns } from "@/hooks/useRuns";
import { formatDateTime, formatMs, formatNumber, shortId } from "@/lib/utils";

const PAGE = 25;
const OUTCOMES: RunOutcome[] = ["answered", "abstained", "clarification", "error"];

export default function RunsPage() {
  const [params, setParams] = useSearchParams();
  const outcome = (OUTCOMES as string[]).includes(params.get("outcome") ?? "") ? (params.get("outcome") as RunOutcome) : undefined;
  const offset = Math.max(0, Number(params.get("offset") ?? 0) || 0);
  const { data, isPending, error, refetch, isFetching } = useRuns(outcome, offset, PAGE);

  const update = (next: Record<string, string | null>) =>
    setParams((p) => {
      for (const [k, v] of Object.entries(next)) {
        if (v === null || v === "") p.delete(k);
        else p.set(k, v);
      }
      return p;
    });

  return (
    <PageContainer>
      <PageHeader
        title="Run inspection"
        description="Every agent run: routing, retrieval attempts, latency and token usage. Admins see the whole workspace; others see their own runs."
        actions={
          <div className="flex items-center gap-2">
            <Label htmlFor="outcome-filter" className="text-xs text-muted-foreground">
              Outcome
            </Label>
            <Select id="outcome-filter" className="w-44" value={outcome ?? ""} onChange={(e) => update({ outcome: e.target.value || null, offset: null })}>
              <option value="">All outcomes</option>
              {OUTCOMES.map((o) => (
                <option key={o} value={o}>
                  {o === "abstained" ? "Abstained" : o.charAt(0).toUpperCase() + o.slice(1)}
                </option>
              ))}
            </Select>
          </div>
        }
      />
      <Card>
        {isPending ? (
          <LoadingRows className="p-5" />
        ) : error ? (
          <div className="p-5">
            <ErrorState error={error} onRetry={() => void refetch()} />
          </div>
        ) : data.items.length === 0 ? (
          <div className="p-5">
            <EmptyState icon={<Activity />} title="No runs yet" description="Runs appear here after questions are asked in chat." />
          </div>
        ) : (
          <>
            <Table aria-busy={isFetching}>
              <THead>
                <tr>
                  <TH>Run</TH>
                  <TH>Outcome</TH>
                  <TH className="hidden md:table-cell">Provider</TH>
                  <TH className="text-right">Latency</TH>
                  <TH className="hidden text-right sm:table-cell">Retrievals</TH>
                  <TH className="hidden text-right lg:table-cell">Tokens (in/out)</TH>
                  <TH className="hidden md:table-cell">When</TH>
                </tr>
              </THead>
              <TBody>
                {data.items.map((run) => (
                  <TR key={run.id}>
                    <TD>
                      <Link to={`/runs/${run.id}`} className="font-mono text-xs font-medium text-primary hover:underline">
                        {shortId(run.id)}
                      </Link>
                      {run.error_category && <p className="text-[11px] text-danger">{run.error_category}</p>}
                    </TD>
                    <TD>
                      <OutcomeBadge outcome={run.outcome} />
                    </TD>
                    <TD className="hidden md:table-cell">
                      <ProviderBadge provider={run.provider} model={run.model} />
                    </TD>
                    <TD className="text-right tabular-nums">{formatMs(run.latency_ms)}</TD>
                    <TD className="hidden text-right tabular-nums sm:table-cell">{run.retrieval_attempts}</TD>
                    <TD className="hidden text-right tabular-nums lg:table-cell">
                      {formatNumber(run.prompt_tokens)} / {formatNumber(run.completion_tokens)}
                    </TD>
                    <TD className="hidden text-xs whitespace-nowrap text-muted-foreground md:table-cell">{formatDateTime(run.created_at)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <div className="flex items-center justify-between border-t border-border px-4 py-2 text-xs text-muted-foreground">
              <span>
                {offset + 1}–{Math.min(offset + PAGE, data.total)} of {formatNumber(data.total)}
              </span>
              <div className="flex gap-1">
                <Button variant="outline" size="icon-sm" aria-label="Previous page" disabled={offset === 0} onClick={() => update({ offset: String(Math.max(0, offset - PAGE)) })}>
                  <ChevronLeft />
                </Button>
                <Button variant="outline" size="icon-sm" aria-label="Next page" disabled={offset + PAGE >= data.total} onClick={() => update({ offset: String(offset + PAGE) })}>
                  <ChevronRight />
                </Button>
              </div>
            </div>
          </>
        )}
      </Card>
    </PageContainer>
  );
}
