import { Database, FileText, Layers, MessageSquare, Plus, Sparkles } from "lucide-react";
import { Link } from "react-router";
import type { Dashboard, DocumentStatus, RunOutcome } from "@/api/types";
import { DocumentStatusBadge } from "@/components/badges";
import { PageHeader, Stat } from "@/components/common";
import { EmptyState, ErrorState } from "@/components/feedback";
import { PageContainer } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Skeleton } from "@/components/ui/primitives";
import { useDashboard } from "@/hooks/useDashboard";
import { useAuth } from "@/lib/auth";
import { formatNumber, formatRelative, humanize } from "@/lib/utils";

const STATUS_ORDER: DocumentStatus[] = ["ready", "processing", "pending", "failed", "unsupported"];
const STATUS_COLOR: Record<DocumentStatus, string> = {
  ready: "bg-success",
  processing: "bg-info",
  pending: "bg-muted-foreground/50",
  failed: "bg-danger",
  unsupported: "bg-warning",
};
const OUTCOME_ORDER: Array<RunOutcome | "unknown"> = ["answered", "abstained", "clarification", "error", "unknown"];
const OUTCOME_COLOR: Record<string, string> = {
  answered: "bg-success",
  abstained: "bg-warning",
  clarification: "bg-info",
  error: "bg-danger",
  unknown: "bg-muted-foreground/50",
};
const OUTCOME_LABEL: Record<string, string> = {
  answered: "Answered",
  abstained: "Not enough evidence",
  clarification: "Clarification",
  error: "Error",
  unknown: "Unknown",
};

/** Horizontal proportional bars; accessible as a plain list of label/value pairs. */
function BreakdownBars({ entries, colors, labels }: { entries: Array<[string, number]>; colors: Record<string, string>; labels?: Record<string, string> }) {
  const total = entries.reduce((sum, [, n]) => sum + n, 0);
  if (total === 0) return <p className="text-sm text-muted-foreground">Nothing yet.</p>;
  return (
    <div className="space-y-3">
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-muted" aria-hidden>
        {entries.map(([key, n]) => (n > 0 ? <div key={key} className={colors[key]} style={{ width: `${(n / total) * 100}%` }} /> : null))}
      </div>
      <ul className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
        {entries.map(([key, n]) => (
          <li key={key} className="flex items-center gap-2">
            <span className={`size-2 rounded-full ${colors[key]}`} aria-hidden />
            <span className="text-muted-foreground">{labels?.[key] ?? humanize(key)}</span>
            <span className="ml-auto font-medium tabular-nums">{n}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const ACTIVITY_LABEL: Record<string, string> = {
  "collection.create": "Created collection",
  "collection.delete": "Deleted collection",
  "document.create": "Added document",
  "document.delete": "Deleted document",
  "document.retry": "Retried ingestion",
  "settings.update": "Updated settings",
  "evaluation.run": "Ran evaluation",
  "conversation.delete": "Deleted conversation",
};

function StatsRow({ data }: { data: Dashboard }) {
  const stats = [
    { label: "Collections", value: data.collections, icon: Database },
    { label: "Documents", value: data.documents, icon: FileText },
    { label: "Chunks indexed", value: data.chunks, icon: Layers },
    { label: "Conversations", value: data.conversations, icon: MessageSquare },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {stats.map(({ label, value, icon: Icon }) => (
        <Card key={label} className="flex items-start justify-between p-4">
          <Stat label={label} value={formatNumber(value)} />
          <Icon className="size-4 text-muted-foreground" aria-hidden />
        </Card>
      ))}
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div className="space-y-4" role="status" aria-label="Loading dashboard">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-22" />
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Skeleton className="h-40" />
        <Skeleton className="h-40" />
      </div>
      <Skeleton className="h-64" />
    </div>
  );
}

export default function DashboardPage() {
  const { data, isPending, error, refetch } = useDashboard();
  const { can } = useAuth();

  return (
    <PageContainer>
      <PageHeader
        title="Workspace overview"
        description="Your knowledge base, agent activity and ingestion health at a glance."
        actions={
          <>
            {can("uploadDocuments") && (
              <Button asChild variant="outline" size="sm">
                <Link to="/knowledge">
                  <Plus aria-hidden /> Add knowledge
                </Link>
              </Button>
            )}
            <Button asChild size="sm">
              <Link to="/chat">
                <Sparkles aria-hidden /> Ask a question
              </Link>
            </Button>
          </>
        }
      />
      {isPending ? (
        <DashboardSkeleton />
      ) : error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : (
        <div className="space-y-4">
          <StatsRow data={data} />
          {data.collections === 0 && (
            <EmptyState
              icon={<Database />}
              title="Start by adding a knowledge source"
              description="Create a collection and upload documents, paste text or add a URL. Atlas will chunk and index it for retrieval."
              action={
                can("createCollections") ? (
                  <Button asChild size="sm">
                    <Link to="/knowledge">Create a collection</Link>
                  </Button>
                ) : undefined
              }
            />
          )}
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Documents by status</CardTitle>
                <CardDescription>Ingestion health across all collections</CardDescription>
              </CardHeader>
              <CardContent>
                <BreakdownBars
                  entries={STATUS_ORDER.map((s) => [s, data.documents_by_status[s] ?? 0])}
                  colors={STATUS_COLOR}
                />
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Agent runs · last 7 days</CardTitle>
                <CardDescription>By outcome — abstentions are honest "not enough evidence" answers</CardDescription>
              </CardHeader>
              <CardContent>
                <BreakdownBars
                  entries={OUTCOME_ORDER.filter((o) => o !== "unknown" || data.runs_last_7_days.unknown).map((o) => [o, data.runs_last_7_days[o] ?? 0])}
                  colors={OUTCOME_COLOR}
                  labels={OUTCOME_LABEL}
                />
                <Link to="/runs" className="mt-3 inline-block text-xs font-medium text-primary hover:underline">
                  Inspect runs →
                </Link>
              </CardContent>
            </Card>
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle>Recent conversations</CardTitle>
              </CardHeader>
              <CardContent>
                {data.recent_conversations.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No conversations yet.</p>
                ) : (
                  <ul className="-mx-2 space-y-0.5">
                    {data.recent_conversations.map((c) => (
                      <li key={c.id}>
                        <Link to={`/chat/${c.id}`} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted">
                          <MessageSquare className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                          <span className="min-w-0 flex-1 truncate">{c.title}</span>
                          <span className="shrink-0 text-xs text-muted-foreground">{formatRelative(c.last_message_at)}</span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Recent documents</CardTitle>
              </CardHeader>
              <CardContent>
                {data.recent_documents.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No documents yet.</p>
                ) : (
                  <ul className="-mx-2 space-y-0.5">
                    {data.recent_documents.map((d) => (
                      <li key={d.id}>
                        <Link
                          to={`/knowledge/${d.collection_id}?doc=${d.id}`}
                          className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted"
                        >
                          <span className="min-w-0 flex-1 truncate">{d.title}</span>
                          <DocumentStatusBadge status={d.status} />
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Activity</CardTitle>
              </CardHeader>
              <CardContent>
                {data.recent_activity.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No activity yet.</p>
                ) : (
                  <ol className="relative space-y-3 border-l border-border pl-4">
                    {data.recent_activity.map((a, i) => (
                      <li key={`${a.created_at}-${i}`} className="text-sm">
                        <span className="absolute -left-[4.5px] mt-1.5 size-2 rounded-full bg-border" aria-hidden />
                        <p>
                          <span className="font-medium">{ACTIVITY_LABEL[a.action] ?? humanize(a.action)}</span>
                          {(a.details.title || a.details.name) && (
                            <span className="text-muted-foreground"> · {a.details.title ?? a.details.name}</span>
                          )}
                        </p>
                        <p className="text-xs text-muted-foreground">{formatRelative(a.created_at)}</p>
                      </li>
                    ))}
                  </ol>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </PageContainer>
  );
}
