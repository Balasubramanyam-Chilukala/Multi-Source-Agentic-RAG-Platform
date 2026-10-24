import { Database, FolderOpen, MoreHorizontal, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { toast } from "sonner";
import type { Collection } from "@/api/types";
import { ConfirmDialog, PageHeader } from "@/components/common";
import { EmptyState, ErrorState } from "@/components/feedback";
import { PageContainer } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/overlays";
import { Badge, Card, Skeleton } from "@/components/ui/primitives";
import { useCollections, useDeleteCollection } from "@/hooks/useKnowledge";
import { useAuth } from "@/lib/auth";
import { formatNumber, formatRelative } from "@/lib/utils";
import { CollectionFormDialog } from "./CollectionDialogs";

function CollectionCard({ collection, onEdit, onDelete }: { collection: Collection; onEdit: () => void; onDelete: () => void }) {
  const { can } = useAuth();
  const showMenu = can("renameCollections") || can("deleteCollections");
  const notReady = collection.document_count - collection.ready_document_count;
  return (
    <Card className="group relative flex flex-col gap-3 p-4 transition-shadow hover:shadow-md focus-within:shadow-md">
      <div className="flex items-start gap-3">
        <div className="rounded-md bg-accent p-2 text-accent-foreground">
          <FolderOpen className="size-4" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-semibold">
            <Link to={`/knowledge/${collection.id}`} className="after:absolute after:inset-0 focus-visible:outline-none">
              {collection.name}
            </Link>
          </h2>
          <p className="line-clamp-2 text-xs text-muted-foreground">{collection.description || "No description"}</p>
        </div>
        {showMenu && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" className="relative z-10" aria-label={`Actions for ${collection.name}`}>
                <MoreHorizontal />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              {can("renameCollections") && (
                <DropdownMenuItem onSelect={onEdit}>
                  <Pencil aria-hidden /> Rename
                </DropdownMenuItem>
              )}
              {can("deleteCollections") && (
                <DropdownMenuItem destructive onSelect={onDelete}>
                  <Trash2 aria-hidden /> Delete
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
      <dl className="grid grid-cols-3 gap-2 text-xs">
        <div>
          <dt className="text-muted-foreground">Documents</dt>
          <dd className="font-semibold tabular-nums">{formatNumber(collection.document_count)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Ready</dt>
          <dd className="font-semibold tabular-nums">{formatNumber(collection.ready_document_count)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Chunks</dt>
          <dd className="font-semibold tabular-nums">{formatNumber(collection.chunk_count)}</dd>
        </div>
      </dl>
      <div className="mt-auto flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
        {notReady > 0 && <Badge tone="info">{notReady} not ready</Badge>}
        {collection.active_embedding_model && (
          <Badge tone="outline" className="max-w-full truncate" title="Embedding model">
            {collection.active_embedding_model}
          </Badge>
        )}
        <span className="ml-auto">Updated {formatRelative(collection.updated_at)}</span>
      </div>
    </Card>
  );
}

export default function CollectionsPage() {
  const { data, isPending, error, refetch } = useCollections();
  const { can } = useAuth();
  const navigate = useNavigate();
  const remove = useDeleteCollection();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Collection | null>(null);
  const [deleting, setDeleting] = useState<Collection | null>(null);

  const createButton = can("createCollections") ? (
    <Button size="sm" onClick={() => setCreating(true)}>
      <Plus aria-hidden /> New collection
    </Button>
  ) : null;

  return (
    <PageContainer>
      <PageHeader
        title="Knowledge sources"
        description="Collections of documents that the assistant can search and cite."
        actions={createButton}
      />
      {isPending ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" role="status" aria-label="Loading collections">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-40" />
          ))}
        </div>
      ) : error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : data.items.length === 0 ? (
        <EmptyState
          icon={<Database />}
          title="No collections yet"
          description={
            can("createCollections")
              ? "Create a collection, then upload files, paste text or add web pages."
              : "Ask a workspace member to create a collection — your role is read-only."
          }
          action={createButton}
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {data.items.map((c) => (
            <CollectionCard key={c.id} collection={c} onEdit={() => setEditing(c)} onDelete={() => setDeleting(c)} />
          ))}
        </div>
      )}

      <CollectionFormDialog open={creating} onOpenChange={setCreating} onCreated={(c) => navigate(`/knowledge/${c.id}`)} />
      {editing && (
        <CollectionFormDialog open onOpenChange={(o) => !o && setEditing(null)} collection={editing} />
      )}
      <ConfirmDialog
        open={Boolean(deleting)}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete “${deleting?.name ?? ""}”?`}
        description={`This permanently deletes the collection and its ${deleting?.document_count ?? 0} document(s) and chunks. Past answers will show their citations as “source deleted”.`}
        confirmLabel="Delete collection"
        onConfirm={async () => {
          if (!deleting) return;
          await remove.mutateAsync(deleting.id);
          toast.success("Collection deleted");
        }}
      />
    </PageContainer>
  );
}
