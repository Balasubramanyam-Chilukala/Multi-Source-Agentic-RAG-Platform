import { CheckCircle2, Link2, Loader2, Type, UploadCloud, XCircle } from "lucide-react";
import { useRef, useState, type DragEvent, type FormEvent } from "react";
import { toast } from "sonner";
import { isApiError } from "@/api/client";
import { InlineError, describeError } from "@/components/feedback";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter } from "@/components/ui/overlays";
import { FieldHint, Input, Label, Select, Textarea } from "@/components/ui/primitives";
import { useCreateTextDocument, useCreateUrlDocument, useUploadDocument } from "@/hooks/useKnowledge";
import { useSystemInfo } from "@/hooks/useSettings";
import { cn, formatBytes } from "@/lib/utils";

export const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".txt", ".md", ".html", ".csv", ".json"];

export function validateFile(file: File, maxBytes?: number): string | null {
  const dot = file.name.lastIndexOf(".");
  const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
  if (!ACCEPTED_EXTENSIONS.includes(ext)) return `Unsupported file type${ext ? ` “${ext}”` : ""}. Accepted: ${ACCEPTED_EXTENSIONS.join(" ")}`;
  if (maxBytes && file.size > maxBytes) return `File is larger than the ${formatBytes(maxBytes)} limit.`;
  if (file.size === 0) return "File is empty.";
  return null;
}

/** The 409 duplicate response carries the id of the document that already has this content. */
export function duplicateDocumentId(error: unknown): string | null {
  if (!isApiError(error) || error.status !== 409) return null;
  const id = error.details[0]?.existing_document_id;
  return typeof id === "string" ? id : null;
}

interface UploadItem {
  key: string;
  name: string;
  size: number;
  state: "uploading" | "done" | "error";
  message?: string;
  existingId?: string | null;
}

export function UploadDropzone({ collectionId, onOpenDocument }: { collectionId: string; onOpenDocument: (id: string) => void }) {
  const upload = useUploadDocument();
  const system = useSystemInfo();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [items, setItems] = useState<UploadItem[]>([]);

  const patch = (key: string, value: Partial<UploadItem>) =>
    setItems((prev) => prev.map((i) => (i.key === key ? { ...i, ...value } : i)));

  function handleFiles(files: FileList | File[]) {
    for (const file of Array.from(files)) {
      const key = `${file.name}-${file.size}-${file.lastModified}-${Math.random()}`;
      const invalid = validateFile(file, system.data?.max_upload_bytes);
      const item: UploadItem = { key, name: file.name, size: file.size, state: invalid ? "error" : "uploading", message: invalid ?? undefined };
      setItems((prev) => [item, ...prev].slice(0, 8));
      if (invalid) continue;
      upload.mutate(
        { collectionId, file },
        {
          onSuccess: (doc) => patch(key, { state: "done", message: "Uploaded — ingestion started", existingId: doc.id }),
          onError: (error) => {
            const existingId = duplicateDocumentId(error);
            const { message, retryAfter } = describeError(error);
            patch(key, {
              state: "error",
              message: existingId ? "This file was already uploaded." : retryAfter ? `${message} Try again in ${retryAfter} s.` : message,
              existingId,
            });
          },
        },
      );
    }
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    if (event.dataTransfer.files.length) handleFiles(event.dataTransfer.files);
  }

  return (
    <div className="space-y-2">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          "flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-6 text-center transition-colors",
          dragging ? "border-primary bg-accent" : "border-border bg-card",
        )}
      >
        <UploadCloud className="size-6 text-muted-foreground" aria-hidden />
        <p className="text-sm">
          <span className="font-medium">Drag files here</span> or{" "}
          <button type="button" className="font-medium text-primary hover:underline" onClick={() => inputRef.current?.click()}>
            browse
          </button>
        </p>
        <p className="text-xs text-muted-foreground">
          {ACCEPTED_EXTENSIONS.join(" ")}
          {system.data ? ` · up to ${formatBytes(system.data.max_upload_bytes)}` : ""}
        </p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPTED_EXTENSIONS.join(",")}
          className="sr-only"
          aria-label="Choose files to upload"
          onChange={(e) => {
            if (e.target.files) handleFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>
      {items.length > 0 && (
        <ul className="space-y-1" aria-live="polite">
          {items.map((item) => (
            <li key={item.key} className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm">
              {item.state === "uploading" ? (
                <Loader2 className="size-4 shrink-0 animate-spin text-info" aria-hidden />
              ) : item.state === "done" ? (
                <CheckCircle2 className="size-4 shrink-0 text-success" aria-hidden />
              ) : (
                <XCircle className="size-4 shrink-0 text-danger" aria-hidden />
              )}
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{item.name}</span>
                <span className={cn("block text-xs", item.state === "error" ? "text-danger" : "text-muted-foreground")}>
                  {item.state === "uploading" ? `Uploading ${formatBytes(item.size)}…` : item.message}
                </span>
              </span>
              {item.existingId && (
                <Button variant="link" size="sm" onClick={() => onOpenDocument(item.existingId as string)}>
                  {item.state === "done" ? "View" : "View existing"}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function PasteTextDialog({ collectionId, open, onOpenChange }: { collectionId: string; open: boolean; onOpenChange: (o: boolean) => void }) {
  const create = useCreateTextDocument();
  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await create.mutateAsync({
        collection_id: collectionId,
        title: String(form.get("title")).trim(),
        text: String(form.get("text")),
        format: form.get("format") === "markdown" ? "markdown" : "text",
      });
      toast.success("Text added — ingestion started");
      onOpenChange(false);
    } catch {
      /* shown inline */
    }
  }
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        create.reset();
        onOpenChange(o);
      }}
    >
      <DialogContent title="Paste text" description="Add notes, policies or any text directly." className="max-w-2xl">
        <form className="space-y-4" onSubmit={onSubmit}>
          <div className="grid gap-4 sm:grid-cols-[1fr_10rem]">
            <div className="space-y-1.5">
              <Label htmlFor="text-title">Title</Label>
              <Input id="text-title" name="title" required maxLength={500} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="text-format">Format</Label>
              <Select id="text-format" name="format" defaultValue="markdown">
                <option value="markdown">Markdown</option>
                <option value="text">Plain text</option>
              </Select>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="text-body">Content</Label>
            <Textarea id="text-body" name="text" required rows={12} className="font-mono text-xs" />
          </div>
          <InlineError error={create.error} />
          {duplicateDocumentId(create.error) && <FieldHint>Identical content already exists in this collection.</FieldHint>}
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={create.isPending}>
              <Type aria-hidden /> Add text
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function AddUrlDialog({ collectionId, open, onOpenChange }: { collectionId: string; open: boolean; onOpenChange: (o: boolean) => void }) {
  const create = useCreateUrlDocument();
  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const title = String(form.get("title") ?? "").trim();
    try {
      await create.mutateAsync({ collection_id: collectionId, url: String(form.get("url")).trim(), ...(title ? { title } : {}) });
      toast.success("URL added — fetching and ingesting");
      onOpenChange(false);
    } catch {
      /* shown inline */
    }
  }
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        create.reset();
        onOpenChange(o);
      }}
    >
      <DialogContent title="Add a web page" description="The server fetches the page (public http/https URLs only) and indexes its text.">
        <form className="space-y-4" onSubmit={onSubmit}>
          <div className="space-y-1.5">
            <Label htmlFor="url-input">URL</Label>
            <Input id="url-input" name="url" type="url" required placeholder="https://example.com/handbook" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="url-title">
              Title <span className="font-normal text-muted-foreground">(optional)</span>
            </Label>
            <Input id="url-title" name="title" maxLength={500} />
          </div>
          <InlineError error={create.error} />
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={create.isPending}>
              <Link2 aria-hidden /> Add URL
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
