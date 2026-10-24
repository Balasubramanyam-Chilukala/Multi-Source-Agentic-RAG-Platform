import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import type { Collection } from "@/api/types";
import { InlineError } from "@/components/feedback";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter } from "@/components/ui/overlays";
import { Input, Label, Textarea } from "@/components/ui/primitives";
import { useCreateCollection, useUpdateCollection } from "@/hooks/useKnowledge";

/** Create (no `collection`) or rename/edit an existing collection. */
export function CollectionFormDialog({
  open,
  onOpenChange,
  collection,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  collection?: Collection;
  onCreated?: (c: Collection) => void;
}) {
  const create = useCreateCollection();
  const update = useUpdateCollection();
  const [error, setError] = useState<unknown>(null);
  const editing = Boolean(collection);
  const pending = create.isPending || update.isPending;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const name = String(form.get("name")).trim();
    const description = String(form.get("description") ?? "").trim() || null;
    setError(null);
    try {
      if (collection) {
        await update.mutateAsync({ id: collection.id, body: { name, description } });
        toast.success("Collection updated");
      } else {
        const created = await create.mutateAsync({ name, description });
        toast.success(`Created “${created.name}”`);
        onCreated?.(created);
      }
      onOpenChange(false);
    } catch (e) {
      setError(e);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setError(null);
        onOpenChange(next);
      }}
    >
      <DialogContent
        title={editing ? "Edit collection" : "New collection"}
        description={editing ? undefined : "Collections group related documents. You can scope chat to one or more of them."}
      >
        <form className="space-y-4" onSubmit={onSubmit}>
          <div className="space-y-1.5">
            <Label htmlFor="collection-name">Name</Label>
            <Input id="collection-name" name="name" required maxLength={200} defaultValue={collection?.name} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="collection-description">
              Description <span className="font-normal text-muted-foreground">(optional)</span>
            </Label>
            <Textarea
              id="collection-description"
              name="description"
              maxLength={2000}
              rows={3}
              defaultValue={collection?.description ?? ""}
            />
          </div>
          <InlineError error={error} />
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={pending}>
              {editing ? "Save" : "Create collection"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
