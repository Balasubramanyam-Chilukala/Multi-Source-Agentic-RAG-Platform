import { ArrowLeft, FileStack, Link2, MessageSquare, Pencil, RefreshCcw, Trash2, Type } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { toast } from "sonner";
import type { DocumentOut } from "@/api/types";
import { ConfirmDialog, PageHeader } from "@/components/common";
import { EmptyState, ErrorState, LoadingRows } from "@/components/feedback";
import { PageContainer } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/primitives";
import { useCollection, useDeleteCollection, useDeleteDocument, useDocuments, useReembedCollection, useRetryDocument } from "@/hooks/useKnowledge";
import { qk } from "@/hooks/keys";
import { isDocumentInFlight } from "@/hooks/polling";
import { useAuth } from "@/lib/auth";
import { formatNumber } from "@/lib/utils";
import { AddUrlDialog, PasteTextDialog, UploadDropzone } from "./AddSources";
import { CollectionFormDialog } from "./CollectionDialogs";
import { DocumentDrawer } from "./DocumentDrawer";
import { DocumentsTable } from "./DocumentsTable";

/** When documents finish ingesting, refresh collection counts (ready/chunks) and the dashboard. */
function useRefreshWhenIngestionSettles(inFlight: number) {
  const client = useQueryClient();
  const previous = useRef(inFlight);
  useEffect(() => {
    if (inFlight < previous.current) {
      void client.invalidateQueries({ queryKey: qk.collections });
      void client.invalidateQueries({ queryKey: qk.dashboard });
    }
    previous.current = inFlight;
  }, [inFlight, client]);
}

export default function CollectionDetailPage() {
  const { collectionId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { can } = useAuth();
  const collection = useCollection(collectionId);
  const documents = useDocuments(collectionId);
  const retry = useRetryDocument();
  const removeDoc = useDeleteDocument();
  const removeCollection = useDeleteCollection();
  const reembed = useReembedCollection();
  const [dialog, setDialog] = useState<"text" | "url" | "edit" | "delete" | "reembed" | null>(null);
  const [deletingDoc, setDeletingDoc] = useState<DocumentOut | null>(null);

  const inFlight = documents.data?.items.filter((d) => isDocumentInFlight(d.status)).length ?? 0;
  useRefreshWhenIngestionSettles(inFlight);

  const openDoc = (id: string) => setParams((p) => { p.set("doc", id); return p; });
  const closeDoc = () => setParams((p) => { p.delete("doc"); return p; });

  if (collection.error) {
    return (
      <PageContainer>
        <ErrorState error={collection.error} onRetry={() => void collection.refetch()} />
      </PageContainer>
    );
  }
  const c = collection.data;

  return (
    <PageContainer>
      <Link to="/knowledge" className="mb-3 inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3.5" aria-hidden /> Knowledge sources
      </Link>
      <PageHeader
        title={c?.name ?? "Loading…"}
        description={c?.description || undefined}
        actions={
          <>
            <Button asChild variant="outline" size="sm">
              <Link to={`/chat?collections=${collectionId}`}>
                <MessageSquare aria-hidden /> Chat with this collection
              </Link>
            </Button>
            {can("renameCollections") && (
              <Button variant="ghost" size="icon" aria-label="Edit collection" onClick={() => setDialog("edit")}>
                <Pencil />
              </Button>
            )}
            {can("reembed") && (
              <Button variant="ghost" size="icon" aria-label="Re-embed collection" title="Re-embed with the current embedding model" onClick={() => setDialog("reembed")}>
                <RefreshCcw />
              </Button>
            )}
            {can("deleteCollections") && (
              <Button variant="ghost" size="icon" aria-label="Delete collection" className="text-danger" onClick={() => setDialog("delete")}>
                <Trash2 />
              </Button>
            )}
          </>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <Card className="order-2 min-w-0 lg:order-1">
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>
              Documents {documents.data && <span className="font-normal text-muted-foreground">({documents.data.total})</span>}
            </CardTitle>
            {inFlight > 0 && (
              <span className="text-xs text-info" aria-live="polite">
                Ingesting {inFlight} document{inFlight === 1 ? "" : "s"}…
              </span>
            )}
          </CardHeader>
          <CardContent className="px-0 pb-0">
            {documents.isPending ? (
              <LoadingRows className="px-5 pb-5" />
            ) : documents.error ? (
              <div className="px-5 pb-5">
                <ErrorState error={documents.error} onRetry={() => void documents.refetch()} />
              </div>
            ) : documents.data.items.length === 0 ? (
              <div className="px-5 pb-5">
                <EmptyState
                  icon={<FileStack />}
                  title="No documents yet"
                  description={can("uploadDocuments") ? "Upload files, paste text, or add a URL to start building this collection." : "This collection is empty."}
                />
              </div>
            ) : (
              <DocumentsTable
                documents={documents.data.items}
                onOpen={openDoc}
                onRetry={(doc) =>
                  retry.mutate(doc.id, { onSuccess: () => toast.success(`Retrying “${doc.title}”`), onError: (e) => toast.error(e.message) })
                }
                onDelete={setDeletingDoc}
              />
            )}
          </CardContent>
        </Card>

        <div className="order-1 space-y-4 lg:order-2">
          {can("uploadDocuments") && (
            <Card>
              <CardHeader>
                <CardTitle>Add knowledge</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <UploadDropzone collectionId={collectionId} onOpenDocument={openDoc} />
                <div className="grid grid-cols-2 gap-2">
                  <Button variant="outline" size="sm" onClick={() => setDialog("text")}>
                    <Type aria-hidden /> Paste text
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => setDialog("url")}>
                    <Link2 aria-hidden /> Add URL
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}
          {c && (
            <Card>
              <CardHeader>
                <CardTitle>Index</CardTitle>
              </CardHeader>
              <CardContent>
                <dl className="grid grid-cols-3 gap-2 text-sm">
                  <div>
                    <dt className="text-xs text-muted-foreground">Documents</dt>
                    <dd className="font-semibold tabular-nums">{formatNumber(c.document_count)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Ready</dt>
                    <dd className="font-semibold tabular-nums">{formatNumber(c.ready_document_count)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Chunks</dt>
                    <dd className="font-semibold tabular-nums">{formatNumber(c.chunk_count)}</dd>
                  </div>
                </dl>
                <p className="mt-3 text-xs text-muted-foreground">
                  Embedding model: <span className="font-mono">{c.active_embedding_model ?? "—"}</span>
                </p>
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      <DocumentDrawer documentId={params.get("doc")} onClose={closeDoc} />
      <PasteTextDialog collectionId={collectionId} open={dialog === "text"} onOpenChange={(o) => setDialog(o ? "text" : null)} />
      <AddUrlDialog collectionId={collectionId} open={dialog === "url"} onOpenChange={(o) => setDialog(o ? "url" : null)} />
      {c && dialog === "edit" && <CollectionFormDialog open collection={c} onOpenChange={(o) => setDialog(o ? "edit" : null)} />}
      <ConfirmDialog
        open={dialog === "delete"}
        onOpenChange={(o) => setDialog(o ? "delete" : null)}
        title={`Delete “${c?.name ?? ""}”?`}
        description="This permanently deletes the collection, its documents and chunks. Past answers will show their citations as “source deleted”."
        confirmLabel="Delete collection"
        onConfirm={async () => {
          await removeCollection.mutateAsync(collectionId);
          toast.success("Collection deleted");
          navigate("/knowledge", { replace: true });
        }}
      />
      <ConfirmDialog
        open={dialog === "reembed"}
        onOpenChange={(o) => setDialog(o ? "reembed" : null)}
        title="Re-embed this collection?"
        description="Every chunk is re-embedded with the server's current embedding model. This runs in the background and uses the embedding provider's quota."
        confirmLabel="Re-embed"
        destructive={false}
        onConfirm={async () => {
          await reembed.mutateAsync(collectionId);
          toast.success("Re-embedding scheduled");
        }}
      />
      <ConfirmDialog
        open={Boolean(deletingDoc)}
        onOpenChange={(o) => !o && setDeletingDoc(null)}
        title={`Delete “${deletingDoc?.title ?? ""}”?`}
        description="The document and its chunks are removed from the index. Existing answers that cite it will show “source deleted”."
        onConfirm={async () => {
          if (!deletingDoc) return;
          await removeDoc.mutateAsync(deletingDoc.id);
          if (params.get("doc") === deletingDoc.id) closeDoc();
          toast.success("Document deleted");
        }}
      />
    </PageContainer>
  );
}
