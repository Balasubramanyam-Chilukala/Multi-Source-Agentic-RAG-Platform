import { Lock, RotateCcw, Save, Server, SlidersHorizontal } from "lucide-react";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { RETRIEVAL_MODES, type SystemInfo, type WorkspaceSettings, type WorkspaceSettingsUpdate } from "@/api/types";
import { ConfirmDialog, KeyValue, PageHeader } from "@/components/common";
import { ErrorState, InlineError, LoadingRows } from "@/components/feedback";
import { PageContainer } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/button";
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, FieldHint, Input, Label, Select } from "@/components/ui/primitives";
import { useSystemInfo, useUpdateSettings, useWorkspaceSettings } from "@/hooks/useSettings";
import { useAuth } from "@/lib/auth";
import { formatBytes, humanize } from "@/lib/utils";

const MODE_HELP: Record<string, string> = {
  dense: "Embedding similarity only",
  lexical: "Full-text keyword search only",
  hybrid: "Dense + lexical fused with RRF",
  hybrid_rerank: "Hybrid, then a cross-encoder reranker",
};

export function validateSettings(v: WorkspaceSettingsUpdate): string | null {
  if (v.top_k != null && (v.top_k < 1 || v.top_k > 20)) return "Top-k must be between 1 and 20.";
  if (v.chunk_size != null && (v.chunk_size < 200 || v.chunk_size > 4000)) return "Chunk size must be between 200 and 4000.";
  if (v.chunk_overlap != null && (v.chunk_overlap < 0 || v.chunk_overlap > 1000)) return "Chunk overlap must be between 0 and 1000.";
  if (v.chunk_size != null && v.chunk_overlap != null && v.chunk_overlap * 2 > v.chunk_size) return "Chunk overlap must be at most half of the chunk size.";
  if (v.min_dense_similarity != null && (v.min_dense_similarity < -1 || v.min_dense_similarity > 1)) return "Minimum dense similarity must be between -1 and 1.";
  return null;
}

function num(form: FormData, key: string): number | null {
  const raw = String(form.get(key) ?? "").trim();
  return raw === "" ? null : Number(raw);
}

function WorkspaceForm({ settings, system, canEdit }: { settings: WorkspaceSettings; system?: SystemInfo; canEdit: boolean }) {
  const update = useUpdateSettings();
  const [clientError, setClientError] = useState<string | null>(null);
  const [resetOpen, setResetOpen] = useState(false);
  const [provider, setProvider] = useState(settings.llm_provider);
  const chatProviders = system?.providers.filter((p) => p.capabilities.includes("chat")) ?? [];
  const rerankAvailable = system ? system.reranker_provider !== "none" : true;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const body: WorkspaceSettingsUpdate = {
      llm_provider: (String(form.get("llm_provider")) || null) as WorkspaceSettingsUpdate["llm_provider"],
      llm_model: String(form.get("llm_model") ?? "").trim() || null,
      retrieval_mode: (String(form.get("retrieval_mode")) || null) as WorkspaceSettingsUpdate["retrieval_mode"],
      top_k: num(form, "top_k"),
      chunk_size: num(form, "chunk_size"),
      chunk_overlap: num(form, "chunk_overlap"),
      min_dense_similarity: num(form, "min_dense_similarity"),
      evaluation_dataset: String(form.get("evaluation_dataset") ?? "").trim() || null,
    };
    const problem = validateSettings(body);
    setClientError(problem);
    if (problem) return;
    try {
      await update.mutateAsync(body);
      toast.success("Workspace settings saved");
    } catch {
      /* shown inline */
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-5" key={JSON.stringify(settings)}>
      <fieldset disabled={!canEdit} className="grid gap-5 sm:grid-cols-2">
        <legend className="sr-only">Workspace settings</legend>
        <div className="space-y-1.5">
          <Label htmlFor="llm_provider">LLM provider</Label>
          <Select id="llm_provider" name="llm_provider" value={provider} onChange={(e) => setProvider(e.target.value)}>
            {(chatProviders.length ? chatProviders : [{ name: settings.llm_provider, configured: true, capabilities: ["chat"] }]).map((p) => (
              <option key={p.name} value={p.name} disabled={!p.configured}>
                {p.name}
                {p.configured ? "" : " (not configured)"}
              </option>
            ))}
          </Select>
          <FieldHint>Only providers configured on the server can be selected.</FieldHint>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="llm_model">LLM model</Label>
          <Input id="llm_model" name="llm_model" defaultValue={settings.llm_model} maxLength={200} />
          <FieldHint>Required when choosing a non-default provider.</FieldHint>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="retrieval_mode">Retrieval mode</Label>
          <Select id="retrieval_mode" name="retrieval_mode" defaultValue={settings.retrieval_mode}>
            {RETRIEVAL_MODES.map((m) => (
              <option key={m} value={m} disabled={m === "hybrid_rerank" && !rerankAvailable}>
                {humanize(m)} — {MODE_HELP[m]}
                {m === "hybrid_rerank" && !rerankAvailable ? " (no reranker configured)" : ""}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="top_k">Top-k passages</Label>
          <Input id="top_k" name="top_k" type="number" min={1} max={20} step={1} defaultValue={settings.top_k} />
          <FieldHint>Passages given to the model per answer (1–20).</FieldHint>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="chunk_size">Chunk size</Label>
          <Input id="chunk_size" name="chunk_size" type="number" min={200} max={4000} step={50} defaultValue={settings.chunk_size} />
          <FieldHint>Characters per chunk (200–4000). Applies to newly ingested documents.</FieldHint>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="chunk_overlap">Chunk overlap</Label>
          <Input id="chunk_overlap" name="chunk_overlap" type="number" min={0} max={1000} step={10} defaultValue={settings.chunk_overlap} />
          <FieldHint>At most half of the chunk size.</FieldHint>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="min_dense_similarity">Minimum dense similarity</Label>
          <Input id="min_dense_similarity" name="min_dense_similarity" type="number" min={-1} max={1} step={0.01} defaultValue={settings.min_dense_similarity} />
          <FieldHint>Evidence below this similarity counts as insufficient (→ abstain).</FieldHint>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="evaluation_dataset">Evaluation dataset</Label>
          <Input id="evaluation_dataset" name="evaluation_dataset" defaultValue={settings.evaluation_dataset} maxLength={200} />
        </div>
      </fieldset>
      {clientError && <InlineError error={new Error(clientError)} />}
      <InlineError error={update.error} />
      {canEdit ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" loading={update.isPending}>
            <Save aria-hidden /> Save settings
          </Button>
          <Button type="button" variant="outline" onClick={() => setResetOpen(true)}>
            <RotateCcw aria-hidden /> Reset to server defaults
          </Button>
          <p className="text-xs text-muted-foreground">Saving pins these values for this workspace.</p>
        </div>
      ) : (
        <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <Lock className="size-3.5" aria-hidden /> Only workspace admins and owners can change settings.
        </p>
      )}
      <ConfirmDialog
        open={resetOpen}
        onOpenChange={setResetOpen}
        title="Reset to server defaults?"
        description="All workspace overrides are removed; the operator's server configuration applies."
        confirmLabel="Reset"
        destructive={false}
        onConfirm={async () => {
          await update.mutateAsync({});
          toast.success("Settings reset to server defaults");
        }}
      />
    </form>
  );
}

function SystemPanel({ system }: { system: SystemInfo }) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Badge tone="outline">Environment: {system.environment}</Badge>
        {system.demo_mode && <Badge tone="warning">Demo mode</Badge>}
      </div>
      <KeyValue
        items={[
          ["Default LLM", `${system.default_llm_provider} · ${system.default_llm_model}`],
          ["Fallback chain", system.fallback_chain.length ? system.fallback_chain.join(" → ") : "—"],
          ["Embeddings", `${system.embedding_provider} · ${system.embedding_model}`],
          ["Embedding dim.", system.embedding_dimension],
          ["Reranker", system.reranker_provider === "none" ? "None" : `${system.reranker_provider}${system.reranker_model ? ` · ${system.reranker_model}` : ""}`],
          ["Max upload", formatBytes(system.max_upload_bytes)],
          ["Agent budget", `${system.agent_max_retrieval_attempts} retrieval attempts · ${system.agent_max_llm_calls} LLM calls`],
        ]}
      />
      <div>
        <h3 className="mb-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">Providers</h3>
        <ul className="space-y-1.5">
          {system.providers.map((p) => (
            <li key={p.name} className="flex items-center gap-2 text-sm">
              <span className={`size-2 rounded-full ${p.configured ? "bg-success" : "bg-muted-foreground/40"}`} aria-hidden />
              <span className="font-medium">{p.name}</span>
              <span className="text-xs text-muted-foreground">{p.capabilities.join(", ")}</span>
              <span className="ml-auto text-xs">{p.configured ? "Configured" : "Not configured"}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export default function SettingsPage() {
  const settings = useWorkspaceSettings();
  const system = useSystemInfo();
  const { can } = useAuth();

  return (
    <PageContainer>
      <PageHeader title="Settings" description="Workspace behaviour you control, and the server configuration it runs on." />
      <div className="grid gap-4 lg:grid-cols-[1fr_24rem]">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <SlidersHorizontal className="size-4" aria-hidden /> Workspace settings
            </CardTitle>
            <CardDescription>
              {can("changeSettings") ? "You can change these." : "Only admins and owners can change these."} Unset values fall back to server defaults.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {settings.isPending ? (
              <LoadingRows rows={6} />
            ) : settings.error ? (
              <ErrorState error={settings.error} onRetry={() => void settings.refetch()} />
            ) : (
              <WorkspaceForm settings={settings.data} system={system.data} canEdit={can("changeSettings")} />
            )}
          </CardContent>
        </Card>
        <Card className="h-fit">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Server className="size-4" aria-hidden /> Server configuration
            </CardTitle>
            <CardDescription>Read-only. Set by the operator through environment variables; secrets are never shown.</CardDescription>
          </CardHeader>
          <CardContent>
            {system.isPending ? (
              <LoadingRows rows={5} />
            ) : system.error ? (
              <ErrorState error={system.error} onRetry={() => void system.refetch()} />
            ) : (
              <SystemPanel system={system.data} />
            )}
          </CardContent>
        </Card>
      </div>
    </PageContainer>
  );
}
