import type { AnswerMetrics, EvaluationRun, RetrievalMetrics } from "@/api/types";

export const RETRIEVAL_METRIC_KEYS: Array<keyof RetrievalMetrics> = ["recall@k", "precision@k", "mrr", "hit_rate@k", "ndcg@k"];
export const ANSWER_METRIC_KEYS: Array<keyof AnswerMetrics> = [
  "correctness",
  "citation_precision",
  "faithfulness",
  "abstention_accuracy",
  "completeness",
];

const LABELS: Record<string, string> = {
  "recall@k": "Recall@k",
  "precision@k": "Precision@k",
  mrr: "MRR",
  "hit_rate@k": "Hit rate@k",
  "ndcg@k": "nDCG@k",
  correctness: "Correctness",
  citation_precision: "Citation precision",
  faithfulness: "Faithfulness",
  abstention_accuracy: "Abstention accuracy",
  completeness: "Completeness",
};

export function metricLabel(key: string, k?: number): string {
  const label = LABELS[key] ?? key;
  return k ? label.replace("@k", `@${k}`) : label;
}

export const MODE_ORDER = ["dense", "lexical", "hybrid", "hybrid_rerank"];

/** Most recent succeeded run per retrieval mode (runs are newest-first from the API). */
export function latestByMode(runs: EvaluationRun[], dataset?: string): EvaluationRun[] {
  const seen = new Map<string, EvaluationRun>();
  const sorted = [...runs].sort((a, b) => b.created_at.localeCompare(a.created_at));
  for (const run of sorted) {
    if (run.status !== "succeeded" || !run.metrics) continue;
    if (dataset && run.dataset_name !== dataset) continue;
    if (!seen.has(run.retrieval_mode)) seen.set(run.retrieval_mode, run);
  }
  return [...seen.values()].sort((a, b) => MODE_ORDER.indexOf(a.retrieval_mode) - MODE_ORDER.indexOf(b.retrieval_mode));
}

export interface ChartRow {
  metric: string;
  [mode: string]: number | string | null;
}

/** Rows for a grouped bar chart: one row per metric, one value per mode (null when missing). */
export function retrievalChartRows(runs: EvaluationRun[]): ChartRow[] {
  const k = runs[0]?.config.top_k;
  return RETRIEVAL_METRIC_KEYS.map((key) => {
    const row: ChartRow = { metric: metricLabel(key, k) };
    for (const run of runs) {
      const value = run.metrics?.retrieval?.[key];
      row[run.retrieval_mode] = typeof value === "number" ? value : null;
    }
    return row;
  });
}
