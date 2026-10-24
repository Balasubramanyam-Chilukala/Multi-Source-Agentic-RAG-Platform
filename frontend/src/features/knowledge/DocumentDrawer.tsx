import { ChevronLeft, ChevronRight, ExternalLink, RotateCcw } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { DocumentStatusBadge } from "@/components/badges";
import { KeyValue } from "@/components/common";
import { ErrorState, LoadingRows } from "@/components/feedback";
import { Button } from "@/components/ui/button";
import { Dialog, SheetContent } from "@/components/ui/overlays";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/controls";
import { Badge } from "@/components/ui/primitives";
import { useDocument, useDocumentChunks, useRetryDocument } from "@/hooks/useKnowledge";
import { useAuth } from "@/lib/auth";
import { formatBytes, formatDateTime, formatNumber, humanize, safeExternalUrl } from "@/lib/utils";
import { DocumentStatusNote } from "./DocumentStatusNote";

const CHUNK_PAGE = 10;

function ChunksTab({ documentId }: { documentId: string }) {
  const [offset, setOffset] = useState(0);
  const { data, isPending, error, refetch, isFetching } = useDocumentChunks(documentId, offset, CHUNK_PAGE);
  if (isPending) return <LoadingRows rows={4} />;
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />;
  if (data.total === 0) return <p className="text-sm text-muted-foreground">No chunks — the document has not been indexed.</p>;
  const page = Math.floor(offset / CHUNK_PAGE) + 1;
  const pages = Math.max(1, Math.ceil(data.total / CHUNK_PAGE));
  return (
    <div className="space-y-3">
      <ol className="space-y-2" aria-busy={isFetching}>
        {data.items.map((chunk) => (
          <li key={chunk.id} className="rounded-md border border-border p-3">
            <div className="mb-1.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
              <Badge tone="outline">#{chunk.ordinal}</Badge>
              {chunk.section && <span className="truncate">§ {chunk.section}</span>}
              {chunk.page_number != null && <span>· p. {chunk.page_number}</span>}
              {chunk.token_count != null && <span className="ml-auto">{chunk.token_count} tokens</span>}
            </div>
            <p className="text-sm leading-relaxed whitespace-pre-wrap">{chunk.text}</p>
          </li>
        ))}
      </ol>
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {formatNumber(data.total)} chunks · page {page} of {pages}
        </span>
        <div className="flex gap-1">
          <Button variant="outline" size="icon-sm" aria-label="Previous chunks" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - CHUNK_PAGE))}>
            <ChevronLeft />
          </Button>
          <Button variant="outline" size="icon-sm" aria-label="Next chunks" disabled={offset + CHUNK_PAGE >= data.total} onClick={() => setOffset(offset + CHUNK_PAGE)}>
            <ChevronRight />
          </Button>
        </div>
      </div>
    </div>
  );
}

export function DocumentDrawer({ documentId, onClose }: { documentId: string | null; onClose: () => void }) {
  const { data: doc, isPending, error, refetch } = useDocument(documentId);
  const retry = useRetryDocument();
  const { can } = useAuth();
  const sourceUrl = safeExternalUrl(doc?.source_uri);

  return (
    <Dialog open={Boolean(documentId)} onOpenChange={(o) => !o && onClose()}>
      <SheetContent title={doc?.title ?? "Document"} description={doc ? `${humanize(doc.source_type)} · ${doc.mime_type ?? "unknown type"}` : undefined}>
        <div className="space-y-4 p-5">
          {isPending ? (
            <LoadingRows rows={5} />
          ) : error ? (
            <ErrorState error={error} onRetry={() => void refetch()} />
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <DocumentStatusBadge status={doc.status} />
                {doc.status === "failed" && can("uploadDocuments") && (
                  <Button
                    size="sm"
                    variant="outline"
                    loading={retry.isPending}
                    onClick={() =>
                      retry.mutate(doc.id, {
                        onSuccess: () => toast.success("Retry scheduled"),
                        onError: (e) => toast.error(e.message),
                      })
                    }
                  >
                    <RotateCcw aria-hidden /> Retry ingestion
                  </Button>
                )}
              </div>
              <DocumentStatusNote document={doc} />
              <Tabs defaultValue="details">
                <TabsList>
                  <TabsTrigger value="details">Details</TabsTrigger>
                  <TabsTrigger value="jobs">Ingestion jobs ({doc.jobs.length})</TabsTrigger>
                  <TabsTrigger value="chunks">Chunks ({doc.chunk_count})</TabsTrigger>
                </TabsList>
                <TabsContent value="details" className="pt-4">
                  <KeyValue
                    items={[
                      ["Source", humanize(doc.source_type)],
                      [
                        "Source URI",
                        sourceUrl ? (
                          <a href={sourceUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">
                            <span className="break-all">{sourceUrl}</span> <ExternalLink className="size-3 shrink-0" aria-hidden />
                          </a>
                        ) : (
                          (doc.source_uri ?? "—")
                        ),
                      ],
                      ["Type", doc.mime_type ?? "—"],
                      ["Size", formatBytes(doc.size_bytes)],
                      ["Pages", doc.page_count ?? "—"],
                      ["Chunks", formatNumber(doc.chunk_count)],
                      ["Content hash", <code key="h" className="font-mono text-xs break-all">{doc.content_hash}</code>],
                      ["Created", formatDateTime(doc.created_at)],
                      ["Updated", formatDateTime(doc.updated_at)],
                    ]}
                  />
                  {Object.keys(doc.metadata ?? {}).length > 0 && (
                    <details className="mt-4 rounded-md border border-border">
                      <summary className="cursor-pointer px-3 py-2 text-sm font-medium">Metadata</summary>
                      <pre className="overflow-x-auto border-t border-border bg-muted px-3 py-2 text-xs">{JSON.stringify(doc.metadata, null, 2)}</pre>
                    </details>
                  )}
                </TabsContent>
                <TabsContent value="jobs" className="pt-4">
                  {doc.jobs.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No ingestion jobs recorded.</p>
                  ) : (
                    <ol className="space-y-2">
                      {doc.jobs.map((job) => (
                        <li key={job.id} className="rounded-md border border-border p-3 text-sm">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-medium">Attempt {job.attempt}</span>
                            <Badge tone={job.status === "succeeded" ? "success" : job.status === "failed" ? "danger" : job.status === "running" ? "info" : "neutral"}>
                              {humanize(job.status)}
                            </Badge>
                            <span className="ml-auto text-xs text-muted-foreground">{formatDateTime(job.created_at)}</span>
                          </div>
                          <p className="mt-1 text-xs text-muted-foreground">
                            Started {formatDateTime(job.started_at)} · Finished {formatDateTime(job.finished_at)}
                          </p>
                          {(job.error_code || job.error_message) && (
                            <p className="mt-1 text-xs text-danger">
                              {job.error_code && <code className="mr-1 font-mono">{job.error_code}</code>}
                              {job.error_message}
                            </p>
                          )}
                        </li>
                      ))}
                    </ol>
                  )}
                </TabsContent>
                <TabsContent value="chunks" className="pt-4">
                  <ChunksTab documentId={doc.id} />
                </TabsContent>
              </Tabs>
            </>
          )}
        </div>
      </SheetContent>
    </Dialog>
  );
}
