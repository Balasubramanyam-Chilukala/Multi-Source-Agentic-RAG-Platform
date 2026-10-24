import { AlertTriangle, Play } from "lucide-react";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { RETRIEVAL_MODES, type EvaluationDataset, type RetrievalMode } from "@/api/types";
import { InlineError } from "@/components/feedback";
import { Button } from "@/components/ui/button";
import { Checkbox, Switch } from "@/components/ui/controls";
import { Dialog, DialogContent, DialogFooter } from "@/components/ui/overlays";
import { FieldHint, Input, Label, Select } from "@/components/ui/primitives";
import { useRunEvaluation } from "@/hooks/useEvaluations";
import { humanize } from "@/lib/utils";

export function RunEvaluationDialog({
  open,
  onOpenChange,
  datasets,
  defaultDataset,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  datasets: EvaluationDataset[];
  defaultDataset?: string;
}) {
  const run = useRunEvaluation();
  const [modes, setModes] = useState<RetrievalMode[]>([...RETRIEVAL_MODES]);
  const [includeAnswers, setIncludeAnswers] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const topK = Number(form.get("top_k"));
    try {
      const result = await run.mutateAsync({
        dataset: String(form.get("dataset")) || undefined,
        modes,
        include_answers: includeAnswers,
        ...(topK ? { top_k: topK } : {}),
      });
      toast.success(`Queued ${result.runs.length} evaluation run${result.runs.length === 1 ? "" : "s"}`);
      onOpenChange(false);
    } catch {
      /* inline */
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        run.reset();
        onOpenChange(o);
      }}
    >
      <DialogContent title="Run evaluation" description="Scores retrieval against the dataset's labelled relevant passages — one run per mode.">
        <form className="space-y-4" onSubmit={onSubmit}>
          <div className="grid gap-4 sm:grid-cols-[1fr_7rem]">
            <div className="space-y-1.5">
              <Label htmlFor="eval-dataset">Dataset</Label>
              <Select id="eval-dataset" name="dataset" defaultValue={defaultDataset ?? datasets[0]?.name}>
                {datasets.length === 0 && <option value="">Server default</option>}
                {datasets.map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.name} (v{d.version}, {d.case_count} cases)
                  </option>
                ))}
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="eval-topk">Top-k</Label>
              <Input id="eval-topk" name="top_k" type="number" min={1} max={20} defaultValue={5} />
            </div>
          </div>
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Retrieval modes</legend>
            <div className="grid grid-cols-2 gap-2">
              {RETRIEVAL_MODES.map((m) => (
                <label key={m} className="flex cursor-pointer items-center gap-2 rounded-md border border-border px-3 py-2 text-sm hover:bg-muted">
                  <Checkbox
                    checked={modes.includes(m)}
                    onCheckedChange={(on) => setModes((prev) => (on === true ? [...prev, m] : prev.filter((x) => x !== m)))}
                  />
                  {humanize(m)}
                </label>
              ))}
            </div>
            {modes.length === 0 && <FieldHint className="text-danger">Select at least one mode.</FieldHint>}
          </fieldset>
          <div className="space-y-2 rounded-md border border-border p-3">
            <label className="flex cursor-pointer items-center justify-between gap-3 text-sm font-medium">
              Also evaluate answers
              <Switch checked={includeAnswers} onCheckedChange={setIncludeAnswers} />
            </label>
            <FieldHint>Generates an answer for every case and scores it with deterministic, reference-based checks.</FieldHint>
            {includeAnswers && (
              <p className="flex items-start gap-1.5 rounded bg-warning-soft px-2 py-1.5 text-xs text-warning">
                <AlertTriangle className="mt-px size-3.5 shrink-0" aria-hidden />
                This calls the configured LLM provider once per case per mode and consumes its (free-tier) quota.
              </p>
            )}
          </div>
          <InlineError error={run.error} />
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={run.isPending} disabled={modes.length === 0}>
              <Play aria-hidden /> Start
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
