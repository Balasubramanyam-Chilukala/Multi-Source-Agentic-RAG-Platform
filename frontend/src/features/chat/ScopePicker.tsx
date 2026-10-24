import { ChevronDown, Database } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/controls";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/overlays";
import { useCollections } from "@/hooks/useKnowledge";

/** Multi-select of collections to search. An empty selection means "all collections". */
export function ScopePicker({ value, onChange, disabled }: { value: string[]; onChange: (ids: string[]) => void; disabled?: boolean }) {
  const { data, isPending } = useCollections();
  const items = data?.items ?? [];
  const selected = items.filter((c) => value.includes(c.id));
  const label =
    value.length === 0 ? "All collections" : selected.length === 1 ? (selected[0]?.name ?? "1 collection") : `${value.length} collections`;

  function toggle(id: string, on: boolean) {
    onChange(on ? [...value, id] : value.filter((v) => v !== id));
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" disabled={disabled} aria-label={`Search scope: ${label}`} className="max-w-56">
          <Database aria-hidden />
          <span className="truncate">{label}</span>
          <ChevronDown aria-hidden className="opacity-60" />
        </Button>
      </PopoverTrigger>
      <PopoverContent>
        <fieldset className="space-y-1">
          <legend className="px-2 pb-1 text-xs font-medium text-muted-foreground">Search in</legend>
          <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-muted">
            <Checkbox checked={value.length === 0} onCheckedChange={() => onChange([])} />
            All collections
          </label>
          {isPending && <p className="px-2 py-1 text-xs text-muted-foreground">Loading…</p>}
          {!isPending && items.length === 0 && <p className="px-2 py-1 text-xs text-muted-foreground">No collections yet.</p>}
          <div className="max-h-64 overflow-y-auto">
            {items.map((c) => (
              <label key={c.id} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-muted">
                <Checkbox checked={value.includes(c.id)} onCheckedChange={(on) => toggle(c.id, on === true)} />
                <span className="min-w-0 flex-1 truncate">{c.name}</span>
                <span className="text-xs text-muted-foreground tabular-nums">{c.ready_document_count}</span>
              </label>
            ))}
          </div>
        </fieldset>
      </PopoverContent>
    </Popover>
  );
}
