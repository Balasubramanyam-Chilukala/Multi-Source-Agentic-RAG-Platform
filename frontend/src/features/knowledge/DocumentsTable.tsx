import { FileText, Globe, MoreHorizontal, RotateCcw, Trash2, Type } from "lucide-react";
import type { DocumentOut } from "@/api/types";
import { DocumentStatusBadge } from "@/components/badges";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/overlays";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/primitives";
import { useAuth } from "@/lib/auth";
import { formatBytes, formatNumber, formatRelative } from "@/lib/utils";
import { DocumentStatusNote } from "./DocumentStatusNote";

const SOURCE_ICON = { upload: FileText, url: Globe, text: Type } as const;

export function DocumentsTable({
  documents,
  onOpen,
  onRetry,
  onDelete,
}: {
  documents: DocumentOut[];
  onOpen: (id: string) => void;
  onRetry: (doc: DocumentOut) => void;
  onDelete: (doc: DocumentOut) => void;
}) {
  const { can } = useAuth();
  const canEdit = can("deleteDocuments");
  return (
    <Table>
      <THead>
        <tr>
          <TH>Document</TH>
          <TH>Status</TH>
          <TH className="hidden text-right md:table-cell">Chunks</TH>
          <TH className="hidden text-right md:table-cell">Size</TH>
          <TH className="hidden sm:table-cell">Updated</TH>
          {canEdit && <TH className="w-10"><span className="sr-only">Actions</span></TH>}
        </tr>
      </THead>
      <TBody>
        {documents.map((doc) => {
          const Icon = SOURCE_ICON[doc.source_type] ?? FileText;
          const retryable = doc.status === "failed"; // the API only retries failed documents
          return (
            <TR key={doc.id}>
              <TD className="max-w-0 w-full">
                <button type="button" onClick={() => onOpen(doc.id)} className="group flex w-full min-w-0 items-start gap-2 text-left">
                  <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="min-w-0">
                    <span className="block truncate font-medium group-hover:text-primary group-hover:underline">{doc.title}</span>
                    <DocumentStatusNote document={doc} compact />
                  </span>
                </button>
              </TD>
              <TD>
                <DocumentStatusBadge status={doc.status} />
              </TD>
              <TD className="hidden text-right tabular-nums md:table-cell">{formatNumber(doc.chunk_count)}</TD>
              <TD className="hidden text-right tabular-nums whitespace-nowrap md:table-cell">{formatBytes(doc.size_bytes)}</TD>
              <TD className="hidden text-xs whitespace-nowrap text-muted-foreground sm:table-cell">{formatRelative(doc.updated_at)}</TD>
              {canEdit && (
                <TD>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${doc.title}`}>
                        <MoreHorizontal />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent>
                      {retryable && (
                        <DropdownMenuItem onSelect={() => onRetry(doc)}>
                          <RotateCcw aria-hidden /> Retry ingestion
                        </DropdownMenuItem>
                      )}
                      <DropdownMenuItem destructive onSelect={() => onDelete(doc)}>
                        <Trash2 aria-hidden /> Delete
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </TD>
              )}
            </TR>
          );
        })}
      </TBody>
    </Table>
  );
}
